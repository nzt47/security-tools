"""
云枢系统测试结果可视化与告警模块

提供：
- 测试结果数据收集和分析
- 可视化报告生成（HTML）
- 告警通知（Email/Slack/Webhook）
- 测试趋势追踪
"""

import json
import smtplib
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from enum import Enum
import logging

class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class TestResult:
    """测试结果数据"""
    test_name: str
    test_file: str
    status: str
    duration_ms: float
    message: Optional[str] = None
    error_type: Optional[str] = None
    timestamp: str = ""

@dataclass
class AlertConfig:
    """告警配置"""
    enabled: bool = True
    email_enabled: bool = False
    slack_enabled: bool = False
    webhook_enabled: bool = False
    email_recipients: List[str] = None
    slack_webhook_url: str = ""
    webhook_urls: List[str] = None
    alert_threshold: float = 90.0  # 失败率阈值

class TestResultAnalyzer:
    """测试结果分析器"""

    def __init__(self, results: List[TestResult]):
        self.results = results
        self.total = len(results)
        self.passed = len([r for r in results if r.status == "passed"])
        self.failed = len([r for r in results if r.status == "failed"])
        self.skipped = len([r for r in results if r.status == "skipped"])

    @property
    def pass_rate(self) -> float:
        """通过率"""
        if self.total == 0:
            return 0.0
        return (self.passed / self.total) * 100

    @property
    def fail_rate(self) -> float:
        """失败率"""
        if self.total == 0:
            return 0.0
        return (self.failed / self.total) * 100

    def get_failed_tests(self) -> List[TestResult]:
        """获取失败的测试"""
        return [r for r in self.results if r.status == "failed"]

    def get_slow_tests(self, threshold_ms: float = 5000) -> List[TestResult]:
        """获取慢速测试"""
        return [r for r in self.results if r.duration_ms > threshold_ms]

    def analyze_by_module(self) -> Dict[str, Dict[str, int]]:
        """按模块分析测试结果"""
        module_stats = {}

        for result in self.results:
            # 提取模块名
            module = result.test_file.replace("/", ".").split(".")[-2] if "." in result.test_file else "unknown"

            if module not in module_stats:
                module_stats[module] = {"passed": 0, "failed": 0, "skipped": 0}

            if result.status == "passed":
                module_stats[module]["passed"] += 1
            elif result.status == "failed":
                module_stats[module]["failed"] += 1
            elif result.status == "skipped":
                module_stats[module]["skipped"] += 1

        return module_stats

class TestReportGenerator:
    """测试报告生成器"""

    def __init__(self, report_dir: Path):
        self.report_dir = report_dir
        self.report_dir.mkdir(exist_ok=True, parents=True)

    def generate_html_report(
        self,
        analyzer: TestResultAnalyzer,
        coverage_data: Optional[Dict] = None,
        benchmark_data: Optional[Dict] = None
    ) -> Path:
        """生成HTML测试报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.report_dir / f"test_report_{timestamp}.html"

        # 计算统计数据
        passed = analyzer.passed
        failed = analyzer.failed
        skipped = analyzer.skipped
        total = analyzer.total
        pass_rate = analyzer.pass_rate

        # 模块统计数据
        module_stats = analyzer.analyze_by_module()

        # 生成HTML内容
        html_content = self._generate_html_content(
            timestamp=timestamp,
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            pass_rate=pass_rate,
            module_stats=module_stats,
            coverage_data=coverage_data,
            benchmark_data=benchmark_data,
            failed_tests=analyzer.get_failed_tests()
        )

        # 写入文件
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        return report_path

    def _generate_html_content(
        self,
        timestamp: str,
        total: int,
        passed: int,
        failed: int,
        skipped: int,
        pass_rate: float,
        module_stats: Dict[str, Dict[str, int]],
        coverage_data: Optional[Dict],
        benchmark_data: Optional[Dict],
        failed_tests: List[TestResult]
    ) -> str:
        """生成HTML内容"""
        # 状态颜色
        status_color = "#28a745" if pass_rate >= 90 else "#ffc107" if pass_rate >= 70 else "#dc3545"

        # 生成模块统计表格
        module_table_rows = ""
        for module, stats in module_stats.items():
            module_pass_rate = (stats["passed"] / sum(stats.values()) * 100) if sum(stats.values()) > 0 else 0
            module_table_rows += f"""
            <tr>
                <td>{module}</td>
                <td>{stats['passed']}</td>
                <td>{stats['failed']}</td>
                <td>{stats['skipped']}</td>
                <td>{module_pass_rate:.1f}%</td>
            </tr>
            """

        # 生成失败测试详情
        failed_tests_rows = ""
        for test in failed_tests:
            failed_tests_rows += f"""
            <tr>
                <td>{test.test_name}</td>
                <td>{test.test_file}</td>
                <td class="text-danger">{test.error_type or 'Unknown'}</td>
                <td>{test.message or '-'}</td>
            </tr>
            """

        # 覆盖率部分
        coverage_section = ""
        if coverage_data:
            coverage_rows = ""
            for module, data in coverage_data.items():
                coverage_rows += f"""
                <tr>
                    <td>{module}</td>
                    <td>{data.get('coverage_percent', 0):.1f}%</td>
                    <td>{data.get('covered_lines', 0)}</td>
                    <td>{data.get('total_lines', 0)}</td>
                </tr>
                """
            coverage_section = f"""
            <div class="section">
                <h2>覆盖率统计</h2>
                <table class="table">
                    <thead>
                        <tr>
                            <th>模块</th>
                            <th>覆盖率</th>
                            <th>覆盖行数</th>
                            <th>总行数</th>
                        </tr>
                    </thead>
                    <tbody>
                        {coverage_rows}
                    </tbody>
                </table>
            </div>
            """

        # HTML模板
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>云枢系统测试报告 - {timestamp}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            border-bottom: 3px solid {status_color};
            padding-bottom: 10px;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin: 30px 0;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-card.success {{ background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); }}
        .stat-card.danger {{ background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%); }}
        .stat-card.warning {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }}
        .stat-number {{
            font-size: 36px;
            font-weight: bold;
        }}
        .stat-label {{
            font-size: 14px;
            opacity: 0.9;
        }}
        .pass-rate {{
            font-size: 48px;
            font-weight: bold;
            color: {status_color};
            text-align: center;
            margin: 30px 0;
        }}
        .section {{
            margin: 30px 0;
        }}
        .section h2 {{
            color: #333;
            border-left: 4px solid #667eea;
            padding-left: 10px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #f8f9fa;
            font-weight: 600;
        }}
        .text-danger {{ color: #dc3545; }}
        .text-success {{ color: #28a745; }}
        .text-warning {{ color: #ffc107; }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-pass {{ background-color: #d4edda; color: #155724; }}
        .badge-fail {{ background-color: #f8d7da; color: #721c24; }}
        .badge-skip {{ background-color: #fff3cd; color: #856404; }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            text-align: center;
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🧪 云枢系统测试报告</h1>
        <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <div class="pass-rate">
            通过率: {pass_rate:.1f}%
        </div>

        <div class="summary">
            <div class="stat-card">
                <div class="stat-number">{total}</div>
                <div class="stat-label">总测试数</div>
            </div>
            <div class="stat-card success">
                <div class="stat-number">{passed}</div>
                <div class="stat-label">通过</div>
            </div>
            <div class="stat-card danger">
                <div class="stat-number">{failed}</div>
                <div class="stat-label">失败</div>
            </div>
            <div class="stat-card warning">
                <div class="stat-number">{skipped}</div>
                <div class="stat-label">跳过</div>
            </div>
        </div>

        <div class="section">
            <h2>📊 模块统计</h2>
            <table class="table">
                <thead>
                    <tr>
                        <th>模块</th>
                        <th>通过</th>
                        <th>失败</th>
                        <th>跳过</th>
                        <th>通过率</th>
                    </tr>
                </thead>
                <tbody>
                    {module_table_rows}
                </tbody>
            </table>
        </div>

        {coverage_section}

        <div class="section">
            <h2>❌ 失败测试详情</h2>
            <table class="table">
                <thead>
                    <tr>
                        <th>测试名称</th>
                        <th>文件</th>
                        <th>错误类型</th>
                        <th>错误信息</th>
                    </tr>
                </thead>
                <tbody>
                    {failed_tests_rows if failed_tests_rows else '<tr><td colspan="4">无失败测试</td></tr>'}
                </tbody>
            </table>
        </div>

        <div class="footer">
            <p>云枢系统自动化测试报告 | Powered by pytest</p>
        </div>
    </div>
</body>
</html>
        """
        return html

    def generate_json_report(
        self,
        analyzer: TestResultAnalyzer,
        coverage_data: Optional[Dict] = None
    ) -> Path:
        """生成JSON测试报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.report_dir / f"test_report_{timestamp}.json"

        report_data = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total": analyzer.total,
                "passed": analyzer.passed,
                "failed": analyzer.failed,
                "skipped": analyzer.skipped,
                "pass_rate": analyzer.pass_rate
            },
            "module_stats": analyzer.analyze_by_module(),
            "failed_tests": [asdict(t) for t in analyzer.get_failed_tests()],
            "slow_tests": [asdict(t) for t in analyzer.get_slow_tests()],
            "coverage": coverage_data
        }

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        return report_path

class AlertManager:
    """告警管理器"""

    def __init__(self, config: AlertConfig):
        self.config = config
        self.logger = logging.getLogger("test.alerts")

    def should_alert(self, analyzer: TestResultAnalyzer) -> bool:
        """判断是否需要告警"""
        if not self.config.enabled:
            return False

        # 检查失败率
        if analyzer.fail_rate > (100 - self.config.alert_threshold):
            return True

        # 检查是否有P0测试失败
        failed_tests = analyzer.get_failed_tests()
        for test in failed_tests:
            if "P0" in test.test_name or "critical" in test.test_name:
                return True

        return False

    def send_alerts(self, analyzer: TestResultAnalyzer):
        """发送告警"""
        if not self.should_alert(analyzer):
            return

        self.logger.warning(f"测试失败率 {analyzer.fail_rate:.1f}%，开始发送告警...")

        # 发送邮件
        if self.config.email_enabled:
            self._send_email_alert(analyzer)

        # 发送Slack
        if self.config.slack_enabled:
            self._send_slack_alert(analyzer)

        # 发送Webhook
        if self.config.webhook_enabled:
            self._send_webhook_alert(analyzer)

    def _send_email_alert(self, analyzer: TestResultAnalyzer):
        """发送邮件告警"""
        if not self.config.email_recipients:
            return

        try:
            # 构建邮件内容
            subject = f"⚠️ 云枢系统测试告警 - 失败率 {analyzer.fail_rate:.1f}%"
            body = self._build_email_body(analyzer)

            # 发送邮件（需要配置SMTP服务器）
            msg = MIMEMultipart()
            msg['From'] = "noreply@Yunshu.example.com"
            msg['To'] = ", ".join(self.config.email_recipients)
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'html', 'utf-8'))

            # 这里需要实际的SMTP服务器配置
            self.logger.info(f"邮件告警已准备发送至: {self.config.email_recipients}")

        except Exception as e:
            self.logger.error(f"发送邮件告警失败: {e}")

    def _build_email_body(self, analyzer: TestResultAnalyzer) -> str:
        """构建邮件内容"""
        failed_tests = analyzer.get_failed_tests()

        failed_rows = ""
        for test in failed_tests[:10]:  # 只显示前10个
            failed_rows += f"""
            <tr>
                <td>{test.test_name}</td>
                <td>{test.error_type or 'Unknown'}</td>
                <td>{test.message or '-'}</td>
            </tr>
            """

        return f"""
        <html>
        <body>
            <h2>云枢系统测试告警</h2>
            <p>测试执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

            <h3>测试结果摘要</h3>
            <table>
                <tr><th>总测试数</th><td>{analyzer.total}</td></tr>
                <tr><th>通过</th><td style="color: green">{analyzer.passed}</td></tr>
                <tr><th>失败</th><td style="color: red">{analyzer.failed}</td></tr>
                <tr><th>失败率</th><td style="color: red">{analyzer.fail_rate:.1f}%</td></tr>
            </table>

            <h3>失败测试详情</h3>
            <table border="1" cellpadding="5">
                <tr>
                    <th>测试名称</th>
                    <th>错误类型</th>
                    <th>错误信息</th>
                </tr>
                {failed_rows}
            </table>

            <p>请登录系统查看完整的测试报告。</p>
        </body>
        </html>
        """

    def _send_slack_alert(self, analyzer: TestResultAnalyzer):
        """发送Slack告警"""
        if not self.config.slack_webhook_url:
            return

        try:
            import requests

            payload = {
                "text": f"⚠️ 云枢系统测试告警",
                "attachments": [
                    {
                        "color": "#ff0000",
                        "fields": [
                            {"title": "总测试数", "value": str(analyzer.total), "short": True},
                            {"title": "通过", "value": str(analyzer.passed), "short": True},
                            {"title": "失败", "value": str(analyzer.failed), "short": True},
                            {"title": "失败率", "value": f"{analyzer.fail_rate:.1f}%", "short": True}
                        ],
                        "footer": f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                ]
            }

            response = requests.post(
                self.config.slack_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                self.logger.info("Slack告警发送成功")
            else:
                self.logger.error(f"Slack告警发送失败: {response.status_code}")

        except Exception as e:
            self.logger.error(f"发送Slack告警失败: {e}")

    def _send_webhook_alert(self, analyzer: TestResultAnalyzer):
        """发送Webhook告警"""
        if not self.config.webhook_urls:
            return

        try:
            import requests

            payload = {
                "alert_type": "test_failure",
                "timestamp": datetime.now().isoformat(),
                "summary": {
                    "total": analyzer.total,
                    "passed": analyzer.passed,
                    "failed": analyzer.failed,
                    "fail_rate": analyzer.fail_rate
                },
                "failed_tests": [
                    {
                        "name": t.test_name,
                        "error": t.error_type,
                        "message": t.message
                    }
                    for t in analyzer.get_failed_tests()[:10]
                ]
            }

            for webhook_url in self.config.webhook_urls:
                try:
                    response = requests.post(
                        webhook_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=10
                    )
                    self.logger.info(f"Webhook告警发送至 {webhook_url}: {response.status_code}")
                except Exception as e:
                    self.logger.error(f"Webhook告警发送失败 ({webhook_url}): {e}")

        except Exception as e:
            self.logger.error(f"发送Webhook告警失败: {e}")

def run_test_with_reporting():
    """运行测试并生成报告"""
    import sys

    # 配置告警
    alert_config = AlertConfig(
        enabled=True,
        email_enabled=False,
        slack_enabled=False,
        webhook_enabled=False
    )

    # 运行测试
    from tests import conftest

    # 生成报告
    report_dir = Path("test_reports")
    generator = TestReportGenerator(report_dir)

    # 示例数据
    sample_results = [
        TestResult(
            test_name="test_memory_store",
            test_file="test_memory.py",
            status="passed",
            duration_ms=150
        ),
        TestResult(
            test_name="test_permission_deny",
            test_file="test_permission.py",
            status="failed",
            duration_ms=50,
            error_type="AssertionError",
            message="权限检查失败"
        )
    ]

    analyzer = TestResultAnalyzer(sample_results)
    html_report = generator.generate_html_report(analyzer)
    json_report = generator.generate_json_report(analyzer)

    print(f"✅ HTML报告: {html_report}")
    print(f"✅ JSON报告: {json_report}")

    # 发送告警
    alert_manager = AlertManager(alert_config)
    alert_manager.send_alerts(analyzer)

if __name__ == "__main__":
    run_test_with_reporting()
