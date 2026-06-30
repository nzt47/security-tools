#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prometheus 指标抓取验证脚本

功能：
1. 验证应用 /metrics 端点正常工作
2. 验证 Prometheus 能正常抓取应用指标
3. 验证关键指标存在且格式正确
4. 生成验证报告

使用方法：
    python scripts/observability_prometheus_verify.py --app-url http://localhost:5678
    python scripts/observability_prometheus_verify.py --prometheus-url http://localhost:9090 --app-url http://localhost:5678
"""

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from typing import Dict, List, Any, Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class PrometheusVerifier:
    """Prometheus 指标验证器"""

    def __init__(self, prometheus_url: str = None, app_url: str = None,
                 output_file: str = None):
        self.prometheus_url = prometheus_url
        self.app_url = app_url.rstrip('/') if app_url else None
        self.output_file = output_file or "prometheus_verify_report.json"
        self.results = {
            "verification_time": datetime.now().isoformat(),
            "prometheus_url": prometheus_url,
            "app_url": app_url,
            "overall_status": "pending",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "checks": {}
        }

    def _record_check(self, check_name: str, status: str, details: Dict = None,
                      error: str = None):
        """记录检查结果"""
        self.results["checks"][check_name] = {
            "status": status,
            "details": details or {},
            "error": error
        }
        if status == "passed":
            self.results["passed"] += 1
        elif status == "failed":
            self.results["failed"] += 1
        else:
            self.results["skipped"] += 1

    def verify_app_metrics_endpoint(self) -> bool:
        """验证应用 /metrics 端点"""
        check_name = "app_metrics_endpoint"

        if not self.app_url:
            self._record_check(check_name, "skipped",
                             error="未提供应用 URL")
            return False

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="requests 库不可用")
            return False

        try:
            url = f"{self.app_url}/metrics"
            response = requests.get(url, timeout=10)

            details = {
                "status_code": response.status_code,
                "content_length": len(response.text),
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code != 200:
                self._record_check(check_name, "failed", details,
                                 error=f"状态码异常: {response.status_code}")
                return False

            # 解析 Prometheus 格式
            content = response.text
            lines = content.split('\n')

            help_lines = [l for l in lines if l.startswith('# HELP')]
            type_lines = [l for l in lines if l.startswith('# TYPE')]
            metric_lines = [l for l in lines if l and not l.startswith('#')]

            details.update({
                "help_lines": len(help_lines),
                "type_lines": len(type_lines),
                "metric_lines": len(metric_lines),
                "total_lines": len(lines),
            })

            # 检查是否有有效的指标数据
            if len(metric_lines) == 0:
                self._record_check(check_name, "failed", details,
                                 error="未找到有效的指标数据")
                return False

            # 提取所有指标名称
            metric_names = set()
            for line in metric_lines:
                if ' ' in line:
                    name_part = line.split(' ')[0]
                    # 移除标签部分
                    if '{' in name_part:
                        name_part = name_part.split('{')[0]
                    metric_names.add(name_part)

            details["metric_count"] = len(metric_names)
            details["metric_names"] = sorted(list(metric_names))[:20]  # 只显示前20个

            self._record_check(check_name, "passed", details)
            return True

        except requests.exceptions.RequestException as e:
            self._record_check(check_name, "failed", error=f"请求失败: {e}")
            return False
        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def verify_key_metrics_exist(self) -> bool:
        """验证关键指标存在"""
        check_name = "key_metrics_exist"

        if not self.app_url or not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="应用 URL 或 requests 库不可用")
            return False

        try:
            url = f"{self.app_url}/metrics"
            response = requests.get(url, timeout=10)
            content = response.text

            # 定义关键指标（前缀匹配）
            key_metrics = [
                "yunshu_",  # 云枢业务指标
                "http_",    # HTTP 请求指标
                "python_",  # Python 运行时指标
            ]

            found_metrics = {}
            missing_metrics = []

            for prefix in key_metrics:
                count = sum(1 for line in content.split('\n')
                          if line.startswith(f'# HELP {prefix}')
                          or line.startswith(f'# TYPE {prefix}')
                          or (line and not line.startswith('#') and line.startswith(prefix)))
                found_metrics[prefix] = count

            # 检查是否有任何业务指标
            has_business_metrics = any(v > 0 for k, v in found_metrics.items()
                                     if k.startswith("yunshu_"))

            details = {
                "found_metrics": found_metrics,
                "has_business_metrics": has_business_metrics,
            }

            if not has_business_metrics:
                self._record_check(check_name, "failed", details,
                                 error="未找到业务指标 (yunshu_ 前缀)")
                return False

            self._record_check(check_name, "passed", details)
            return True

        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def verify_prometheus_connection(self) -> bool:
        """验证 Prometheus 连接"""
        check_name = "prometheus_connection"

        if not self.prometheus_url:
            self._record_check(check_name, "skipped",
                             error="未提供 Prometheus URL")
            return False

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="requests 库不可用")
            return False

        try:
            # 检查 Prometheus 是否健康
            url = f"{self.prometheus_url}/-/healthy"
            response = requests.get(url, timeout=10)

            details = {
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
            }

            if response.status_code != 200:
                self._record_check(check_name, "failed", details,
                                 error=f"Prometheus 健康检查失败: {response.status_code}")
                return False

            # 检查 Prometheus 版本信息
            url = f"{self.prometheus_url}/api/v1/status/buildinfo"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                details["version"] = data.get("data", {}).get("version", "unknown")
                details["revision"] = data.get("data", {}).get("revision", "unknown")

            self._record_check(check_name, "passed", details)
            return True

        except requests.exceptions.RequestException as e:
            self._record_check(check_name, "failed", error=f"连接失败: {e}")
            return False
        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def verify_prometheus_targets(self) -> bool:
        """验证 Prometheus 抓取目标"""
        check_name = "prometheus_targets"

        if not self.prometheus_url:
            self._record_check(check_name, "skipped",
                             error="未提供 Prometheus URL")
            return False

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="requests 库不可用")
            return False

        try:
            url = f"{self.prometheus_url}/api/v1/targets"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                self._record_check(check_name, "failed",
                                 error=f"获取目标列表失败: {response.status_code}")
                return False

            data = response.json()
            targets = data.get("data", {}).get("activeTargets", [])

            details = {
                "total_targets": len(targets),
                "targets": []
            }

            up_targets = 0
            for target in targets:
                target_info = {
                    "job": target.get("labels", {}).get("job", "unknown"),
                    "instance": target.get("labels", {}).get("instance", "unknown"),
                    "health": target.get("health", "unknown"),
                    "last_scrape": target.get("lastScrape", "unknown"),
                    "scrape_url": target.get("scrapeUrl", "unknown"),
                }
                details["targets"].append(target_info)
                if target.get("health") == "up":
                    up_targets += 1

            details["up_targets"] = up_targets
            details["down_targets"] = len(targets) - up_targets

            # 检查是否有应用目标
            app_target_found = any(
                "yunshu" in t.get("job", "").lower()
                for t in details["targets"]
            )
            details["app_target_found"] = app_target_found

            if up_targets == 0:
                self._record_check(check_name, "failed", details,
                                 error="没有处于 UP 状态的抓取目标")
                return False

            self._record_check(check_name, "passed", details)
            return True

        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def verify_prometheus_rules(self) -> bool:
        """验证 Prometheus 告警规则加载"""
        check_name = "prometheus_rules"

        if not self.prometheus_url:
            self._record_check(check_name, "skipped",
                             error="未提供 Prometheus URL")
            return False

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="requests 库不可用")
            return False

        try:
            url = f"{self.prometheus_url}/api/v1/rules"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                self._record_check(check_name, "failed",
                                 error=f"获取规则失败: {response.status_code}")
                return False

            data = response.json()
            groups = data.get("data", {}).get("groups", [])

            details = {
                "total_groups": len(groups),
                "total_rules": 0,
                "groups": []
            }

            for group in groups:
                rules = group.get("rules", [])
                details["total_rules"] += len(rules)

                group_info = {
                    "name": group.get("name", "unknown"),
                    "file": group.get("file", "unknown"),
                    "rule_count": len(rules),
                    "rule_names": [r.get("name", r.get("alert", "unknown")) for r in rules]
                }
                details["groups"].append(group_info)

            if details["total_rules"] == 0:
                self._record_check(check_name, "failed", details,
                                 error="未加载任何告警规则")
                return False

            self._record_check(check_name, "passed", details)
            return True

        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def verify_metric_query(self) -> bool:
        """验证指标查询功能"""
        check_name = "metric_query"

        if not self.prometheus_url:
            self._record_check(check_name, "skipped",
                             error="未提供 Prometheus URL")
            return False

        if not REQUESTS_AVAILABLE:
            self._record_check(check_name, "skipped",
                             error="requests 库不可用")
            return False

        try:
            # 查询 up 指标（这是 Prometheus 内置的，一定存在）
            url = f"{self.prometheus_url}/api/v1/query"
            params = {"query": "up"}
            response = requests.get(url, params=params, timeout=10)

            if response.status_code != 200:
                self._record_check(check_name, "failed",
                                 error=f"查询失败: {response.status_code}")
                return False

            data = response.json()
            result = data.get("data", {}).get("result", [])

            details = {
                "query": "up",
                "result_count": len(result),
                "status": data.get("status", "unknown"),
            }

            if data.get("status") != "success":
                self._record_check(check_name, "failed", details,
                                 error="查询返回非成功状态")
                return False

            self._record_check(check_name, "passed", details)
            return True

        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def run_all_verifications(self) -> Dict:
        """运行所有验证"""
        print("\n" + "=" * 70)
        print("📊 Prometheus 指标验证")
        print("=" * 70)
        print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if self.prometheus_url:
            print(f"Prometheus: {self.prometheus_url}")
        if self.app_url:
            print(f"应用地址: {self.app_url}")

        verifiers = [
            ("应用 /metrics 端点", self.verify_app_metrics_endpoint),
            ("关键指标存在性", self.verify_key_metrics_exist),
            ("Prometheus 连接", self.verify_prometheus_connection),
            ("抓取目标状态", self.verify_prometheus_targets),
            ("告警规则加载", self.verify_prometheus_rules),
            ("指标查询功能", self.verify_metric_query),
        ]

        for name, verifier in verifiers:
            print(f"\n{'─' * 50}")
            print(f"验证: {name}")
            try:
                result = verifier()
                status_icon = "✅" if result else "❌"
                print(f"结果: {status_icon} {'通过' if result else '失败'}")
            except Exception as e:
                print(f"结果: ❌ 异常 - {e}")
                traceback.print_exc()

        # 计算总体状态
        if self.results["failed"] > 0:
            self.results["overall_status"] = "failed"
        elif self.results["passed"] > 0:
            self.results["overall_status"] = "passed"
        else:
            self.results["overall_status"] = "skipped"

        # 保存报告
        self._save_report()

        return self.results

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
        print("📊 验证结果汇总")
        print("=" * 70)

        print(f"总检查项: {self.results['passed'] + self.results['failed'] + self.results['skipped']}")
        print(f"✅ 通过: {self.results['passed']}")
        print(f"❌ 失败: {self.results['failed']}")
        print(f"⚠️  跳过: {self.results['skipped']}")

        if self.results["failed"] > 0:
            print(f"\n❌ 整体状态: 失败")
            print("\n失败的检查项:")
            for name, check in self.results["checks"].items():
                if check["status"] == "failed":
                    print(f"  - {name}: {check.get('error', '未知错误')}")
        else:
            print(f"\n✅ 整体状态: 通过")

        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Prometheus 指标抓取验证")
    parser.add_argument("--prometheus-url", default=None,
                       help="Prometheus 服务器地址")
    parser.add_argument("--app-url", default="http://localhost:5678",
                       help="应用服务地址")
    parser.add_argument("--output", default="prometheus_verify_report.json",
                       help="输出报告文件路径")
    args = parser.parse_args()

    verifier = PrometheusVerifier(
        prometheus_url=args.prometheus_url,
        app_url=args.app_url,
        output_file=args.output
    )

    try:
        verifier.run_all_verifications()
        verifier.print_summary()

        if verifier.results["failed"] > 0:
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
