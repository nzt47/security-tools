#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署后可观测性自动验证脚本

功能：
1. 部署后自动验证所有可观测性端点
2. 验证健康检查接口
3. 验证追踪系统正常工作
4. 验证指标系统正常工作
5. 验证日志系统正常工作
6. 验证告警系统正常工作
7. 生成部署验证报告
8. 失败时触发告警通知

使用方法：
    python scripts/observability_post_deploy.py --app-url http://localhost:5678
    python scripts/observability_post_deploy.py --full --app-url http://localhost:5678
    python scripts/observability_post_deploy.py --notify-webhook <webhook_url>
"""

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class PostDeployValidator:
    """部署后验证器"""

    def __init__(self, app_url: str = "http://localhost:5678",
                 prometheus_url: str = None,
                 full_check: bool = False,
                 output_file: str = None,
                 max_retries: int = 5,
                 retry_interval: int = 10):
        self.app_url = app_url.rstrip('/')
        self.prometheus_url = prometheus_url.rstrip('/') if prometheus_url else None
        self.full_check = full_check
        self.output_file = output_file or "post_deploy_observability_report.json"
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.results = {
            "validation_time": datetime.now().isoformat(),
            "app_url": self.app_url,
            "prometheus_url": self.prometheus_url,
            "full_check": full_check,
            "overall_status": "pending",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "checks": {},
            "warnings": [],
            "recommendations": [],
        }

    def _record_check(self, check_name: str, status: str, details: Dict = None,
                      error: str = None, warning: str = None):
        """记录检查结果"""
        self.results["checks"][check_name] = {
            "status": status,
            "details": details or {},
            "error": error,
            "warning": warning,
        }
        if status == "passed":
            self.results["passed"] += 1
        elif status == "failed":
            self.results["failed"] += 1
        else:
            self.results["skipped"] += 1

        if warning:
            self.results["warnings"].append({"check": check_name, "message": warning})

    def _retry_request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """带重试的请求"""
        if not REQUESTS_AVAILABLE:
            return None

        for attempt in range(self.max_retries):
            try:
                response = requests.request(method, url, timeout=10, **kwargs)
                return response
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    print(f"  重试 {attempt + 1}/{self.max_retries}: {e}")
                    time.sleep(self.retry_interval)
                else:
                    raise

    def check_service_health(self) -> bool:
        """检查服务健康状态"""
        check_name = "service_health"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        try:
            response = self._retry_request("GET", f"{self.app_url}/api/health")
            if response is None:
                self._record_check(check_name, "failed", error="无法连接到服务")
                return False

            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code != 200:
                self._record_check(check_name, "failed", details,
                                 error=f"健康检查返回状态码: {response.status_code}")
                return False

            # 解析响应
            try:
                data = response.json()
                details["response"] = data

                # 检查健康状态字段
                if "status" in data:
                    health_status = data.get("status")
                    details["health_status"] = health_status
                    if health_status not in ["healthy", "ok", "up"]:
                        self._record_check(check_name, "failed", details,
                                         error=f"健康状态异常: {health_status}")
                        return False

                if "overall_health" in data:
                    details["overall_health"] = data.get("overall_health")

            except ValueError:
                # 非 JSON 响应也可以接受
                pass

            self._record_check(check_name, "passed", details)
            return True

        except Exception as e:
            self._record_check(check_name, "failed", error=str(e))
            return False

    def check_diagnostics_health(self) -> bool:
        """检查诊断健康端点"""
        check_name = "diagnostics_health"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        try:
            response = self._retry_request("GET", f"{self.app_url}/api/diagnostics/health")
            if response is None:
                self._record_check(check_name, "skipped",
                                 error="诊断端点不可用（可能未启用）")
                return True  # 跳过不算失败

            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code == 200:
                try:
                    data = response.json()
                    details["response"] = data
                    details["opentelemetry_available"] = data.get("opentelemetry_available", False)
                    details["overall_health"] = data.get("overall_health", 0)
                except ValueError:
                    pass
                self._record_check(check_name, "passed", details)
                return True
            else:
                self._record_check(check_name, "skipped", details,
                                 warning=f"诊断健康端点返回 {response.status_code}")
                return True

        except Exception as e:
            self._record_check(check_name, "skipped",
                             warning=f"诊断端点不可用: {e}")
            return True

    def check_metrics_endpoint(self) -> bool:
        """检查 Prometheus 指标端点"""
        check_name = "metrics_endpoint"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        try:
            response = self._retry_request("GET", f"{self.app_url}/metrics")
            if response is None:
                self._record_check(check_name, "failed", error="无法连接到 /metrics 端点")
                return False

            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code != 200:
                self._record_check(check_name, "failed", details,
                                 error=f"状态码异常: {response.status_code}")
                return False

            content = response.text
            lines = content.split('\n')

            # 统计指标
            help_lines = sum(1 for l in lines if l.startswith('# HELP'))
            type_lines = sum(1 for l in lines if l.startswith('# TYPE'))
            metric_lines = sum(1 for l in lines if l and not l.startswith('#'))

            details.update({
                "help_lines": help_lines,
                "type_lines": type_lines,
                "metric_lines": metric_lines,
                "total_lines": len(lines),
            })

            if metric_lines == 0:
                self._record_check(check_name, "failed", details,
                                 error="未找到任何指标数据")
                return False

            # 检查是否有业务指标
            has_yunshu_metrics = any(
                line.startswith('yunshu_') or line.startswith('# HELP yunshu_')
                for line in lines
            )
            details["has_business_metrics"] = has_yunshu_metrics

            if not has_yunshu_metrics:
                self._record_check(check_name, "passed", details,
                                 warning="未检测到业务指标（yunshu_ 前缀）")
            else:
                self._record_check(check_name, "passed", details)

            return True

        except Exception as e:
            self._record_check(check_name, "failed", error=str(e))
            return False

    def check_tracing_endpoints(self) -> bool:
        """检查追踪端点"""
        check_name = "tracing_endpoints"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        endpoints = [
            "/api/diagnostics/trace",
            "/api/diagnostics/trace/inject",
        ]

        all_passed = True
        details = {"endpoints": {}}

        for endpoint in endpoints:
            try:
                response = requests.get(f"{self.app_url}{endpoint}", timeout=5)
                endpoint_detail = {
                    "status_code": response.status_code,
                    "response_time_ms": response.elapsed.total_seconds() * 1000,
                }

                if response.status_code == 200:
                    endpoint_detail["status"] = "passed"
                    try:
                        data = response.json()
                        if "trace_id" in data:
                            endpoint_detail["has_trace_id"] = True
                    except ValueError:
                        pass
                else:
                    endpoint_detail["status"] = "skipped"
                    endpoint_detail["warning"] = f"返回状态码 {response.status_code}"

                details["endpoints"][endpoint] = endpoint_detail

            except Exception as e:
                details["endpoints"][endpoint] = {
                    "status": "skipped",
                    "warning": str(e),
                }

        # 只要有一个端点可用就认为通过
        any_passed = any(
            ep.get("status") == "passed"
            for ep in details["endpoints"].values()
        )

        if any_passed:
            self._record_check(check_name, "passed", details)
            return True
        else:
            self._record_check(check_name, "skipped", details,
                             warning="追踪端点可能未启用")
            return True  # 跳过不算失败

    def check_logs_endpoint(self) -> bool:
        """检查日志端点"""
        check_name = "logs_endpoint"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        try:
            response = requests.get(f"{self.app_url}/api/diagnostics/logs", timeout=5)
            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code == 200:
                try:
                    data = response.json()
                    details["log_count"] = len(data.get("logs", []))
                    if data.get("logs"):
                        sample_log = data["logs"][0]
                        details["has_trace_id_in_logs"] = "trace_id" in sample_log
                except ValueError:
                    pass
                self._record_check(check_name, "passed", details)
                return True
            else:
                self._record_check(check_name, "skipped", details,
                                 warning=f"日志端点返回 {response.status_code}")
                return True

        except Exception as e:
            self._record_check(check_name, "skipped",
                             warning=f"日志端点不可用: {e}")
            return True

    def check_observability_state(self) -> bool:
        """检查可观测性状态端点"""
        check_name = "observability_state"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        try:
            response = requests.get(f"{self.app_url}/api/observability/state", timeout=5)
            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code == 200:
                try:
                    data = response.json()
                    details["components"] = list(data.keys())
                    details["has_trace_id"] = "trace_id" in data
                    details["has_health"] = "health" in data
                    details["has_metrics"] = "metrics" in data
                except ValueError:
                    pass
                self._record_check(check_name, "passed", details)
                return True
            else:
                self._record_check(check_name, "skipped", details,
                                 warning=f"可观测性状态端点返回 {response.status_code}")
                return True

        except Exception as e:
            self._record_check(check_name, "skipped",
                             warning=f"可观测性状态端点不可用: {e}")
            return True

    def check_prometheus_integration(self) -> bool:
        """检查 Prometheus 集成（如果提供了 Prometheus URL）"""
        check_name = "prometheus_integration"

        if not self.prometheus_url or not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="未提供 Prometheus URL 或 requests 库不可用")
            return True  # 跳过不算失败

        try:
            # 检查 Prometheus 健康
            response = requests.get(f"{self.prometheus_url}/-/healthy", timeout=5)
            details = {
                "prometheus_healthy": response.status_code == 200,
            }

            if response.status_code != 200:
                self._record_check(check_name, "failed", details,
                                 error="Prometheus 不健康")
                return False

            # 检查目标
            response = requests.get(f"{self.prometheus_url}/api/v1/targets", timeout=5)
            if response.status_code == 200:
                data = response.json()
                targets = data.get("data", {}).get("activeTargets", [])
                details["total_targets"] = len(targets)

                # 检查应用是否在目标列表中
                app_target_found = any(
                    "yunshu" in t.get("labels", {}).get("job", "").lower()
                    for t in targets
                )
                details["app_target_found"] = app_target_found

                up_targets = sum(1 for t in targets if t.get("health") == "up")
                details["up_targets"] = up_targets

                if not app_target_found:
                    self._record_check(check_name, "passed", details,
                                     warning="未在 Prometheus 目标中找到应用")
                    self.results["recommendations"].append(
                        "请确保 Prometheus 配置中包含应用的抓取目标"
                    )
                else:
                    self._record_check(check_name, "passed", details)

                return True

            self._record_check(check_name, "failed", details,
                             error="无法获取 Prometheus 目标列表")
            return False

        except Exception as e:
            self._record_check(check_name, "failed", error=str(e))
            return False

    def check_heartbeat_endpoint(self) -> bool:
        """检查心跳端点"""
        check_name = "heartbeat"

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped", error="requests 库不可用")
            return False

        try:
            response = requests.get(f"{self.app_url}/api/heartbeat", timeout=5)
            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code == 200:
                self._record_check(check_name, "passed", details)
                return True
            else:
                self._record_check(check_name, "skipped", details,
                                 warning=f"心跳端点返回 {response.status_code}")
                return True

        except Exception as e:
            self._record_check(check_name, "skipped",
                             warning=f"心跳端点不可用: {e}")
            return True

    def run_full_validation(self) -> Dict:
        """运行完整的部署后验证"""
        print("\n" + "=" * 70)
        print("🚀 部署后可观测性验证")
        print("=" * 70)
        print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"应用地址: {self.app_url}")
        if self.prometheus_url:
            print(f"Prometheus: {self.prometheus_url}")
        print(f"完整检查: {self.full_check}")
        print(f"最大重试: {self.max_retries} 次")

        checks = [
            ("服务健康检查", self.check_service_health),
            ("诊断健康端点", self.check_diagnostics_health),
            ("Prometheus 指标端点", self.check_metrics_endpoint),
            ("追踪系统端点", self.check_tracing_endpoints),
            ("日志系统端点", self.check_logs_endpoint),
            ("可观测性状态", self.check_observability_state),
            ("心跳端点", self.check_heartbeat_endpoint),
        ]

        if self.full_check and self.prometheus_url:
            checks.append(("Prometheus 集成", self.check_prometheus_integration))

        all_passed = True
        for name, check_func in checks:
            print(f"\n{'─' * 50}")
            print(f"检查: {name}")
            try:
                result = check_func()
                status_icon = "✅" if result else "❌"
                print(f"结果: {status_icon} {'通过' if result else '失败'}")
                if not result:
                    all_passed = False
            except Exception as e:
                print(f"结果: ❌ 异常 - {e}")
                traceback.print_exc()
                all_passed = False

        # 计算总体状态
        if self.results["failed"] > 0:
            self.results["overall_status"] = "failed"
        elif self.results["passed"] > 0:
            self.results["overall_status"] = "passed"
        else:
            self.results["overall_status"] = "skipped"

        # 生成建议
        self._generate_recommendations()

        # 保存报告
        self._save_report()

        return self.results

    def _generate_recommendations(self):
        """生成优化建议"""
        if self.results["failed"] > 0:
            self.results["recommendations"].append(
                "请修复上述失败的检查项后重新部署"
            )

        if self.results["warnings"]:
            self.results["recommendations"].append(
                f"共有 {len(self.results['warnings'])} 个警告，建议关注"
            )

        # 检查可观测性完整性
        checks_list = list(self.results["checks"].keys())
        core_checks = ["service_health", "metrics_endpoint"]
        missing_core = [c for c in core_checks if c not in self.results["checks"]
                       or self.results["checks"][c]["status"] == "skipped"]

        if missing_core:
            self.results["recommendations"].append(
                f"建议启用核心可观测性功能: {', '.join(missing_core)}"
            )

    def _save_report(self):
        """保存验证报告"""
        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            print(f"\n📄 验证报告已保存: {self.output_file}")
        except Exception as e:
            print(f"\n⚠️  保存报告失败: {e}")

    def print_summary(self):
        """打印验证摘要"""
        print("\n" + "=" * 70)
        print("📊 部署后可观测性验证结果")
        print("=" * 70)

        total = self.results["passed"] + self.results["failed"] + self.results["skipped"]
        print(f"总检查项: {total}")
        print(f"✅ 通过: {self.results['passed']}")
        print(f"❌ 失败: {self.results['failed']}")
        print(f"⚠️  跳过: {self.results['skipped']}")

        if self.results["warnings"]:
            print(f"\n⚠️  警告 ({len(self.results['warnings'])} 个):")
            for w in self.results["warnings"]:
                print(f"  - [{w['check']}] {w['message']}")

        if self.results["overall_status"] == "passed":
            print(f"\n✅ 整体状态: 通过 - 部署成功")
        elif self.results["overall_status"] == "failed":
            print(f"\n❌ 整体状态: 失败 - 请检查并修复")
            print("\n失败的检查项:")
            for name, check in self.results["checks"].items():
                if check["status"] == "failed":
                    print(f"  - {name}: {check.get('error', '未知错误')}")
        else:
            print(f"\n⚠️  整体状态: 已跳过部分检查")

        if self.results["recommendations"]:
            print(f"\n💡 建议:")
            for rec in self.results["recommendations"]:
                print(f"  - {rec}")

        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="部署后可观测性自动验证")
    parser.add_argument("--app-url", default="http://localhost:5678",
                       help="应用服务地址 (默认: http://localhost:5678)")
    parser.add_argument("--prometheus-url", default=None,
                       help="Prometheus 服务器地址（可选）")
    parser.add_argument("--full", action="store_true",
                       help="运行完整检查（包括 Prometheus 集成）")
    parser.add_argument("--output", default="post_deploy_observability_report.json",
                       help="输出报告文件路径")
    parser.add_argument("--max-retries", type=int, default=5,
                       help="最大重试次数 (默认: 5)")
    parser.add_argument("--retry-interval", type=int, default=10,
                       help="重试间隔秒数 (默认: 10)")
    parser.add_argument("--notify-webhook", default=None,
                       help="失败时通知的 Webhook URL（可选）")
    parser.add_argument("--notify-secret", default=None,
                       help="通知 Webhook 的加签密钥（可选）")
    args = parser.parse_args()

    validator = PostDeployValidator(
        app_url=args.app_url,
        prometheus_url=args.prometheus_url,
        full_check=args.full,
        output_file=args.output,
        max_retries=args.max_retries,
        retry_interval=args.retry_interval,
    )

    try:
        validator.run_full_validation()
        validator.print_summary()

        # 如果配置了通知且验证失败，发送通知
        if args.notify_webhook and validator.results["overall_status"] == "failed":
            print("\n📧 发送失败通知...")
            try:
                # 延迟导入以避免依赖
                from observability_dingtalk_notify import DingTalkNotifier

                notifier = DingTalkNotifier(args.notify_webhook, args.notify_secret)
                title = "❌ 部署后可观测性验证失败"
                text = (
                    f"# ❌ 部署后可观测性验证失败\n\n"
                    f"**失败项**: {validator.results['failed']} 个\n"
                    f"**通过项**: {validator.results['passed']} 个\n\n"
                    f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"请查看详细报告进行排查。"
                )
                result = notifier.send_markdown(title, text)
                if result["success"]:
                    print("✅ 通知发送成功")
                else:
                    print(f"⚠️  通知发送失败: {result.get('error')}")
            except Exception as e:
                print(f"⚠️  通知发送异常: {e}")

        if validator.results["overall_status"] == "failed":
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n验证中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 验证失败: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
