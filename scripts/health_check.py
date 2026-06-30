#!/usr/bin/env python3
"""云枢系统健康度一键检查脚本

功能：
1. 系统级健康检查（CPU、内存、磁盘、网络）
2. 服务级健康检查（各模块状态、依赖可用性）
3. 业务级健康检查（错误率、延迟、质量评分）
4. 输出彩色健康报告
5. 支持 JSON 格式输出（用于自动化）

用法：
    python scripts/health_check.py          # 彩色报告
    python scripts/health_check.py --json   # JSON格式
    python scripts/health_check.py --quiet  # 仅返回退出码
    python scripts/health_check.py --detail # 详细输出

退出码：
    0 - 健康（所有检查通过）
    1 - 警告（部分指标异常）
    2 - 危险（严重问题）
    3 - 检查失败（脚本错误）
"""

import os
import sys
import json
import time
import logging
import argparse
import platform
from datetime import datetime
from typing import Dict, List, Any, Optional

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


class Colors:
    """终端颜色"""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    @classmethod
    def disable(cls):
        for attr in dir(cls):
            if not attr.startswith('_'):
                setattr(cls, attr, '')


class HealthStatus:
    """健康状态枚举"""
    HEALTHY = "healthy"    # 健康
    WARNING = "warning"    # 警告
    CRITICAL = "critical"  # 危险
    UNKNOWN = "unknown"    # 未知

    @classmethod
    def get_color(cls, status: str) -> str:
        return {
            cls.HEALTHY: Colors.GREEN,
            cls.WARNING: Colors.YELLOW,
            cls.CRITICAL: Colors.RED,
            cls.UNKNOWN: Colors.MAGENTA,
        }.get(status, Colors.WHITE)

    @classmethod
    def get_icon(cls, status: str) -> str:
        return {
            cls.HEALTHY: "✅",
            cls.WARNING: "⚠️",
            cls.CRITICAL: "🔴",
            cls.UNKNOWN: "❓",
        }.get(status, "❓")

    @classmethod
    def from_score(cls, score: float) -> str:
        if score >= 90:
            return cls.HEALTHY
        elif score >= 70:
            return cls.HEALTHY
        elif score >= 50:
            return cls.WARNING
        elif score >= 30:
            return cls.WARNING
        else:
            return cls.CRITICAL


@dataclass
class CheckResult:
    """检查结果"""
    name: str
    category: str
    status: str
    message: str
    details: Dict[str, Any] = None
    score: float = 100.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "message": self.message,
            "details": self.details or {},
            "score": self.score,
            "error": self.error,
        }


class HealthChecker:
    """健康检查器"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results: List[CheckResult] = []

    def check_all(self) -> List[CheckResult]:
        """执行所有检查"""
        self.results = []

        self._check_system()
        self._check_services()
        self._check_business()
        self._check_security()

        return self.results

    def _check_system(self):
        """系统级检查"""
        category = "system"

        if not _PSUTIL_AVAILABLE:
            self.results.append(CheckResult(
                name="psutil检查",
                category=category,
                status=HealthStatus.WARNING,
                message="psutil 未安装，跳过系统资源检查",
                score=0,
            ))
            return

        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_status = HealthStatus.HEALTHY if cpu_percent < 70 else (
            HealthStatus.WARNING if cpu_percent < 85 else HealthStatus.CRITICAL
        )
        cpu_score = 100 if cpu_percent < 50 else (80 if cpu_percent < 70 else (50 if cpu_percent < 85 else 20))
        self.results.append(CheckResult(
            name="CPU使用率",
            category=category,
            status=cpu_status,
            message=f"CPU 使用率: {cpu_percent:.1f}%",
            details={"cpu_percent": cpu_percent},
            score=cpu_score,
        ))

        mem = psutil.virtual_memory()
        mem_percent = mem.percent
        mem_status = HealthStatus.HEALTHY if mem_percent < 70 else (
            HealthStatus.WARNING if mem_percent < 85 else HealthStatus.CRITICAL
        )
        mem_score = 100 if mem_percent < 60 else (80 if mem_percent < 75 else (50 if mem_percent < 85 else 20))
        self.results.append(CheckResult(
            name="内存使用率",
            category=category,
            status=mem_status,
            message=f"内存使用率: {mem_percent:.1f}% ({mem.used / 1024**3:.1f}GB / {mem.total / 1024**3:.1f}GB)",
            details={"memory_percent": mem_percent, "memory_used_gb": mem.used / 1024**3,
                     "memory_total_gb": mem.total / 1024**3},
            score=mem_score,
        ))

        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_status = HealthStatus.HEALTHY if disk_percent < 70 else (
            HealthStatus.WARNING if disk_percent < 85 else HealthStatus.CRITICAL
        )
        disk_score = 100 if disk_percent < 60 else (80 if disk_percent < 75 else (50 if disk_percent < 85 else 20))
        self.results.append(CheckResult(
            name="磁盘使用率",
            category=category,
            status=disk_status,
            message=f"磁盘使用率: {disk_percent:.1f}% ({disk.used / 1024**3:.1f}GB / {disk.total / 1024**3:.1f}GB)",
            details={"disk_percent": disk_percent, "disk_used_gb": disk.used / 1024**3,
                     "disk_total_gb": disk.total / 1024**3},
            score=disk_score,
        ))

        load_avg = psutil.getloadavg() if hasattr(psutil, 'getloadavg') else (0, 0, 0)
        cpu_count = psutil.cpu_count() or 1
        load_status = HealthStatus.HEALTHY if load_avg[1] < cpu_count * 0.7 else (
            HealthStatus.WARNING if load_avg[1] < cpu_count else HealthStatus.CRITICAL
        )
        self.results.append(CheckResult(
            name="系统负载",
            category=category,
            status=load_status,
            message=f"负载: {load_avg[0]:.2f} / {load_avg[1]:.2f} / {load_avg[2]:.2f} (1m/5m/15m)",
            details={"load_1m": load_avg[0], "load_5m": load_avg[1], "load_15m": load_avg[2],
                     "cpu_count": cpu_count},
            score=100 if load_status == HealthStatus.HEALTHY else (60 if load_status == HealthStatus.WARNING else 30),
        ))

        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        self.results.append(CheckResult(
            name="系统运行时间",
            category=category,
            status=HealthStatus.HEALTHY,
            message=f"系统已运行: {uptime.days}天 {uptime.seconds // 3600}小时",
            details={"uptime_seconds": uptime.total_seconds(), "boot_time": boot_time.isoformat()},
            score=100,
        ))

    def _check_services(self):
        """服务级检查"""
        category = "service"

        endpoints = [
            ("/health", "服务健康检查"),
            ("/api/diagnostics/health", "诊断健康检查"),
            ("/api/metrics", "指标接口"),
        ]

        if not _REQUESTS_AVAILABLE:
            self.results.append(CheckResult(
                name="HTTP检查",
                category=category,
                status=HealthStatus.WARNING,
                message="requests 未安装，跳过服务健康检查",
                score=0,
            ))
            return

        for endpoint, name in endpoints:
            try:
                url = f"{self.base_url}{endpoint}"
                start_time = time.time()
                response = requests.get(url, timeout=10)
                elapsed = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    status = HealthStatus.HEALTHY
                    score = 100 if elapsed < 500 else (80 if elapsed < 1000 else 60)
                    message = f"{name}: 正常 ({elapsed:.0f}ms)"
                else:
                    status = HealthStatus.WARNING
                    score = 40
                    message = f"{name}: HTTP {response.status_code}"

                self.results.append(CheckResult(
                    name=name,
                    category=category,
                    status=status,
                    message=message,
                    details={"endpoint": endpoint, "status_code": response.status_code,
                             "response_time_ms": elapsed},
                    score=score,
                ))

            except requests.exceptions.ConnectionError:
                self.results.append(CheckResult(
                    name=name,
                    category=category,
                    status=HealthStatus.CRITICAL,
                    message=f"{name}: 连接失败",
                    error="connection_refused",
                    score=0,
                ))
            except requests.exceptions.Timeout:
                self.results.append(CheckResult(
                    name=name,
                    category=category,
                    status=HealthStatus.CRITICAL,
                    message=f"{name}: 超时",
                    error="timeout",
                    score=0,
                ))
            except Exception as e:
                self.results.append(CheckResult(
                    name=name,
                    category=category,
                    status=HealthStatus.UNKNOWN,
                    message=f"{name}: 错误 - {str(e)}",
                    error=str(e),
                    score=0,
                ))

        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yunshu_logs.db")
        if os.path.exists(db_path):
            db_size = os.path.getsize(db_path) / 1024 / 1024
            self.results.append(CheckResult(
                name="日志数据库",
                category=category,
                status=HealthStatus.HEALTHY if db_size < 1000 else HealthStatus.WARNING,
                message=f"日志数据库大小: {db_size:.1f}MB",
                details={"db_size_mb": db_size},
                score=100 if db_size < 500 else (70 if db_size < 1000 else 40),
            ))

    def _check_business(self):
        """业务级检查"""
        category = "business"

        if not _REQUESTS_AVAILABLE:
            return

        try:
            url = f"{self.base_url}/api/dashboard/quality"
            response = requests.get(url, timeout=10)

            if response.status_code == 200:
                data = response.json()

                schema_pass_rate = data.get("schema_pass_rate", 0.95)
                schema_status = HealthStatus.HEALTHY if schema_pass_rate >= 0.95 else (
                    HealthStatus.WARNING if schema_pass_rate >= 0.85 else HealthStatus.CRITICAL
                )
                self.results.append(CheckResult(
                    name="Schema校验通过率",
                    category=category,
                    status=schema_status,
                    message=f"Schema通过率: {schema_pass_rate:.1%}",
                    details={"schema_pass_rate": schema_pass_rate},
                    score=schema_pass_rate * 100,
                ))

                critic_score = data.get("critic_score", 80)
                critic_status = HealthStatus.HEALTHY if critic_score >= 80 else (
                    HealthStatus.WARNING if critic_score >= 60 else HealthStatus.CRITICAL
                )
                self.results.append(CheckResult(
                    name="Critic质量评分",
                    category=category,
                    status=critic_status,
                    message=f"Critic评分: {critic_score:.1f}/100",
                    details={"critic_score": critic_score},
                    score=critic_score,
                ))

                error_rate = data.get("error_rate", 0.01)
                error_status = HealthStatus.HEALTHY if error_rate <= 0.03 else (
                    HealthStatus.WARNING if error_rate <= 0.05 else HealthStatus.CRITICAL
                )
                self.results.append(CheckResult(
                    name="错误率",
                    category=category,
                    status=error_status,
                    message=f"错误率: {error_rate:.2%}",
                    details={"error_rate": error_rate},
                    score=max(0, 100 - error_rate * 1000),
                ))

            else:
                self.results.append(CheckResult(
                    name="业务质量数据",
                    category=category,
                    status=HealthStatus.UNKNOWN,
                    message=f"无法获取质量数据: HTTP {response.status_code}",
                    score=0,
                ))

        except Exception as e:
            self.results.append(CheckResult(
                name="业务质量检查",
                category=category,
                status=HealthStatus.UNKNOWN,
                message=f"检查失败: {str(e)}",
                error=str(e),
                score=0,
            ))

    def _check_security(self):
        """安全检查"""
        category = "security"

        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_file):
            self.results.append(CheckResult(
                name="环境配置",
                category=category,
                status=HealthStatus.HEALTHY,
                message=".env 文件存在",
                details={"env_exists": True},
                score=100,
            ))
        else:
            self.results.append(CheckResult(
                name="环境配置",
                category=category,
                status=HealthStatus.WARNING,
                message=".env 文件不存在，使用默认配置",
                details={"env_exists": False},
                score=60,
            ))

        config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".secure_config.json")
        if os.path.exists(config_file):
            perms = oct(os.stat(config_file).st_mode)[-3:]
            self.results.append(CheckResult(
                name="安全配置权限",
                category=category,
                status=HealthStatus.WARNING if perms not in ('600', '400') else HealthStatus.HEALTHY,
                message=f"安全配置权限: {perms}",
                details={"permissions": perms},
                score=100 if perms in ('600', '400') else 50,
            ))

        self.results.append(CheckResult(
            name="Python版本",
            category=category,
            status=HealthStatus.HEALTHY if sys.version_info >= (3, 9) else HealthStatus.WARNING,
            message=f"Python版本: {platform.python_version()}",
            details={"python_version": platform.python_version()},
            score=100 if sys.version_info >= (3, 10) else (80 if sys.version_info >= (3, 9) else 50),
        ))

    def get_summary(self) -> Dict[str, Any]:
        """获取汇总信息"""
        categories = {}
        for result in self.results:
            cat = result.category
            if cat not in categories:
                categories[cat] = {"total": 0, "healthy": 0, "warning": 0,
                                    "critical": 0, "unknown": 0, "score_sum": 0}
            categories[cat]["total"] += 1
            categories[cat][result.status] += 1
            categories[cat]["score_sum"] += result.score

        overall_score = 0
        total_weight = 0
        for cat, data in categories.items():
            if data["total"] > 0:
                avg_score = data["score_sum"] / data["total"]
                weight = {"system": 0.25, "service": 0.35, "business": 0.25, "security": 0.15}.get(cat, 0.25)
                overall_score += avg_score * weight
                total_weight += weight

        overall_score = overall_score / total_weight if total_weight > 0 else 0

        critical_count = sum(1 for r in self.results if r.status == HealthStatus.CRITICAL)
        warning_count = sum(1 for r in self.results if r.status == HealthStatus.WARNING)

        if critical_count > 0:
            overall_status = HealthStatus.CRITICAL
        elif warning_count > 2:
            overall_status = HealthStatus.WARNING
        elif warning_count > 0:
            overall_status = HealthStatus.WARNING
        else:
            overall_status = HealthStatus.HEALTHY

        return {
            "timestamp": datetime.now().isoformat(),
            "overall_score": round(overall_score, 1),
            "overall_status": overall_status,
            "checks_count": len(self.results),
            "critical_count": critical_count,
            "warning_count": warning_count,
            "healthy_count": sum(1 for r in self.results if r.status == HealthStatus.HEALTHY),
            "categories": {
                cat: {
                    "total": data["total"],
                    "healthy": data["healthy"],
                    "warning": data["warning"],
                    "critical": data["critical"],
                    "avg_score": round(data["score_sum"] / data["total"], 1) if data["total"] > 0 else 0,
                }
                for cat, data in categories.items()
            },
            "issues": [
                {"name": r.name, "status": r.status, "message": r.message}
                for r in self.results
                if r.status in (HealthStatus.CRITICAL, HealthStatus.WARNING)
            ],
        }

    def print_report(self, detail: bool = False):
        """打印彩色报告"""
        summary = self.get_summary()
        status_color = HealthStatus.get_color(summary["overall_status"])
        status_icon = HealthStatus.get_icon(summary["overall_status"])

        print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}  云枢系统健康度检查报告{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")
        print(f"  检查时间: {summary['timestamp']}")
        print(f"  检查项数: {summary['checks_count']}")
        print()

        print(f"{Colors.BOLD}  综合健康度: {status_color}{status_icon} {summary['overall_score']:.1f}/100 "
              f"({summary['overall_status']}){Colors.RESET}")
        print()

        categories = {
            "system": "💻 系统资源",
            "service": "🔧 服务状态",
            "business": "📊 业务质量",
            "security": "🔒 安全配置",
        }

        for cat_key, cat_name in categories.items():
            cat_data = summary["categories"].get(cat_key)
            if not cat_data:
                continue

            cat_score = cat_data["avg_score"]
            cat_status = HealthStatus.from_score(cat_score)
            cat_color = HealthStatus.get_color(cat_status)
            cat_icon = HealthStatus.get_icon(cat_status)

            print(f"\n{Colors.BOLD}  {cat_name}{Colors.RESET}  "
                  f"{cat_color}{cat_icon} {cat_score:.1f}/100{Colors.RESET}")

            if detail:
                for result in self.results:
                    if result.category != cat_key:
                        continue
                    status_color = HealthStatus.get_color(result.status)
                    status_icon = HealthStatus.get_icon(result.status)
                    print(f"    {status_color}{status_icon} {result.name}: {result.message}{Colors.RESET}")

        print(f"\n{Colors.BOLD}  问题汇总:{Colors.RESET}")
        if summary["critical_count"] > 0:
            print(f"    {Colors.RED}🔴 严重问题: {summary['critical_count']} 个{Colors.RESET}")
        if summary["warning_count"] > 0:
            print(f"    {Colors.YELLOW}⚠️  警告: {summary['warning_count']} 个{Colors.RESET}")
        if summary["critical_count"] == 0 and summary["warning_count"] == 0:
            print(f"    {Colors.GREEN}✅ 所有检查通过！{Colors.RESET}")

        if summary["issues"] and detail:
            print(f"\n{Colors.BOLD}  详细问题列表:{Colors.RESET}")
            for i, issue in enumerate(summary["issues"], 1):
                issue_color = HealthStatus.get_color(issue["status"])
                print(f"    {i}. {issue_color}{issue['name']}{Colors.RESET}: {issue['message']}")

        print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")

        return summary


def main():
    parser = argparse.ArgumentParser(description="云枢系统健康度检查")
    parser.add_argument("--url", default="http://localhost:8000", help="服务基础URL")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    parser.add_argument("--quiet", action="store_true", help="静默模式，仅返回退出码")
    parser.add_argument("--detail", action="store_true", help="显示详细信息")
    parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        Colors.disable()

    try:
        checker = HealthChecker(base_url=args.url)
        checker.check_all()
        summary = checker.get_summary()

        if args.json:
            output = {
                "summary": summary,
                "checks": [r.to_dict() for r in checker.results],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
        elif not args.quiet:
            checker.print_report(detail=args.detail)

        if summary["overall_status"] == HealthStatus.CRITICAL:
            sys.exit(2)
        elif summary["overall_status"] == HealthStatus.WARNING:
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n检查已中断")
        sys.exit(130)
    except Exception as e:
        print(f"检查失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(3)


if __name__ == "__main__":
    main()
