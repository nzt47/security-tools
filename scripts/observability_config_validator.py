#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可观测性配置文件验证脚本

功能：
1. 验证 Prometheus 配置文件格式和完整性
2. 验证告警规则配置
3. 验证追踪系统配置
4. 验证日志系统配置
5. 生成验证报告

使用方法：
    python scripts/observability_config_validator.py
    python scripts/observability_config_validator.py --config-dir monitoring/
    python scripts/observability_config_validator.py --output report.json
"""

import argparse
import json
import logging
import os
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

# ── 结构化日志配置（与 visibility_report.py 降级日志模式对齐） ──
logger = logging.getLogger("obs_config_validator")


def _trace_id() -> str:
    """生成简易 trace_id（无第三方依赖）"""
    return uuid.uuid4().hex[:16]


class ObservabilityConfigValidator:
    """可观测性配置验证器"""

    def __init__(self, config_dir: str = "monitoring", output_file: str = None):
        self.config_dir = Path(config_dir)
        self.output_file = output_file or "observability_config_report.json"
        self.results = {
            "validation_time": datetime.now().isoformat(),
            "config_dir": str(self.config_dir),
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

    def validate_prometheus_config(self) -> bool:
        """验证 Prometheus 主配置"""
        check_name = "prometheus_config"
        config_file = self.config_dir / "prometheus.yml"

        if not config_file.exists():
            self._record_check(check_name, "skipped",
                             error=f"配置文件不存在: {config_file}")
            return False

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            details = {
                "file_exists": True,
                "yaml_valid": True,
                "has_global": "global" in config,
                "has_rule_files": "rule_files" in config,
                "has_scrape_configs": "scrape_configs" in config,
                "scrape_config_count": len(config.get("scrape_configs", [])),
                "rule_file_count": len(config.get("rule_files", [])),
            }

            # 验证必要字段
            required_fields = ["global", "scrape_configs"]
            missing_fields = [f for f in required_fields if f not in config]

            if missing_fields:
                self._record_check(check_name, "failed", details,
                                 error=f"缺少必要字段: {missing_fields}")
                return False

            # 验证 scrape_configs 格式
            for sc in config.get("scrape_configs", []):
                if "job_name" not in sc:
                    self._record_check(check_name, "failed", details,
                                     error="scrape_config 缺少 job_name")
                    return False

            self._record_check(check_name, "passed", details)
            return True

        except yaml.YAMLError as e:
            self._record_check(check_name, "failed", error=f"YAML 解析错误: {e}")
            return False
        except Exception as e:
            self._record_check(check_name, "failed", error=f"验证异常: {e}")
            return False

    def validate_alert_rules(self) -> bool:
        """验证告警规则配置"""
        check_name = "alert_rules"
        alert_files = list(self.config_dir.glob("alerts*.yml"))

        if not alert_files:
            self._record_check(check_name, "skipped",
                             error="未找到告警规则文件")
            return False

        all_passed = True
        total_rules = 0
        alerts_by_severity = {}
        alerts_by_category = {}

        for alert_file in alert_files:
            try:
                with open(alert_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)

                if "groups" not in config:
                    self._record_check(check_name, "failed",
                                     error=f"{alert_file.name} 缺少 groups 字段")
                    all_passed = False
                    continue

                for group in config["groups"]:
                    rules = group.get("rules", [])
                    total_rules += len(rules)

                    for rule in rules:
                        if "alert" in rule:
                            severity = rule.get("labels", {}).get("severity", "unknown")
                            alerts_by_severity[severity] = alerts_by_severity.get(severity, 0) + 1

                            # 验证必要字段
                            if "expr" not in rule:
                                self._record_check(check_name, "failed",
                                                 error=f"告警规则 {rule['alert']} 缺少 expr")
                                all_passed = False

                            if "for" not in rule:
                                self._record_check(check_name, "failed",
                                                 error=f"告警规则 {rule['alert']} 缺少 for 字段")
                                all_passed = False

            except yaml.YAMLError as e:
                self._record_check(check_name, "failed",
                                 error=f"{alert_file.name} YAML 解析错误: {e}")
                all_passed = False

        details = {
            "alert_files": [f.name for f in alert_files],
            "total_rules": total_rules,
            "alerts_by_severity": alerts_by_severity,
            "alerts_by_category": alerts_by_category,
        }

        if all_passed:
            self._record_check(check_name, "passed", details)
        else:
            self._record_check(check_name, "failed", details,
                             error="部分告警规则验证失败")

        return all_passed

    def validate_tracing_config(self) -> bool:
        """验证追踪系统配置"""
        check_name = "tracing_config"

        # 检查 tracing 模块是否存在
        tracing_module = Path("agent/monitoring/tracing.py")
        tracing_module_alt = Path("agent/observability/tracer.py")

        details = {
            "tracing_module_exists": tracing_module.exists(),
            "observability_module_exists": tracing_module_alt.exists(),
        }

        if not tracing_module.exists() and not tracing_module_alt.exists():
            self._record_check(check_name, "failed", details,
                             error="追踪模块不存在")
            return False

        # 检查 OpenTelemetry 依赖
        try:
            import opentelemetry
            details["opentelemetry_available"] = True
            details["opentelemetry_version"] = getattr(opentelemetry, "__version__", "unknown")
        except ImportError:
            details["opentelemetry_available"] = False

        self._record_check(check_name, "passed", details)
        return True

    def validate_metrics_config(self) -> bool:
        """验证指标系统配置"""
        check_name = "metrics_config"

        details = {}

        # 检查 Prometheus exporter 模块
        exporter_module = Path("agent/prometheus_exporter.py")
        details["exporter_module_exists"] = exporter_module.exists()

        # 检查 prometheus_client 依赖
        try:
            import prometheus_client
            details["prometheus_client_available"] = True
            details["prometheus_client_version"] = getattr(prometheus_client, "__version__", "unknown")
        except ImportError:
            details["prometheus_client_available"] = False

        if not exporter_module.exists():
            self._record_check(check_name, "failed", details,
                             error="Prometheus exporter 模块不存在")
            return False

        self._record_check(check_name, "passed", details)
        return True

    def validate_logging_config(self) -> bool:
        """验证日志系统配置"""
        check_name = "logging_config"

        details = {}

        # 检查日志模块
        log_module = Path("agent/log_system")
        details["log_system_exists"] = log_module.exists()

        # 检查日志配置文件
        log_config = Path("config.yaml")
        if log_config.exists():
            try:
                with open(log_config, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                details["has_logging_config"] = "logging" in str(config).lower()
            except Exception as e:
                # 结构化日志：配置解析失败降级，不静默吞掉异常（原 bare except 会掩盖所有错误）
                logger.error(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "obs_config_validator",
                    "action": "validate_logging_config.parse_failed",
                    "duration_ms": 0,
                    "path": str(log_config),
                    "error": f"{type(e).__name__}: {e}",
                }, ensure_ascii=False))
                details["has_logging_config"] = False
        else:
            details["has_logging_config"] = False

        self._record_check(check_name, "passed", details)
        return True

    def validate_dashboard_config(self) -> bool:
        """验证仪表盘配置"""
        check_name = "dashboard_config"

        details = {
            "has_monitoring_dir": self.config_dir.exists(),
        }

        # 检查 Grafana 配置
        grafana_dir = self.config_dir / "grafana"
        details["grafana_dir_exists"] = grafana_dir.exists()

        if grafana_dir.exists():
            dashboards = list(grafana_dir.glob("**/*.json"))
            details["dashboard_count"] = len(dashboards)
            details["dashboards"] = [d.name for d in dashboards]

        self._record_check(check_name, "passed", details)
        return True

    def run_all_validations(self) -> Dict:
        """运行所有验证"""
        print("\n" + "=" * 70)
        print("🔍 可观测性配置验证")
        print("=" * 70)
        print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"配置目录: {self.config_dir}")

        validators = [
            ("Prometheus 配置", self.validate_prometheus_config),
            ("告警规则配置", self.validate_alert_rules),
            ("追踪系统配置", self.validate_tracing_config),
            ("指标系统配置", self.validate_metrics_config),
            ("日志系统配置", self.validate_logging_config),
            ("仪表盘配置", self.validate_dashboard_config),
        ]

        for name, validator in validators:
            print(f"\n{'─' * 50}")
            print(f"验证: {name}")
            try:
                result = validator()
                status_icon = "✅" if result else "❌"
                print(f"结果: {status_icon} {'通过' if result else '失败'}")
            except Exception as e:
                print(f"结果: ❌ 异常 - {e}")
                traceback.print_exc()

        # 计算总体状态
        total = self.results["passed"] + self.results["failed"] + self.results["skipped"]
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

        total = self.results["passed"] + self.results["failed"] + self.results["skipped"]
        print(f"总检查项: {total}")
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
    parser = argparse.ArgumentParser(description="可观测性配置文件验证")
    parser.add_argument("--config-dir", default="monitoring",
                       help="配置文件目录 (默认: monitoring/)")
    parser.add_argument("--output", default="observability_config_report.json",
                       help="输出报告文件路径")
    args = parser.parse_args()

    validator = ObservabilityConfigValidator(args.config_dir, args.output)

    try:
        validator.run_all_validations()
        validator.print_summary()

        if validator.results["failed"] > 0:
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
