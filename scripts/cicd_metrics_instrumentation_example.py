"""
CI/CD 流水线指标埋点示例

[用途] 展示如何在 CI/CD 脚本中调用 PrometheusMetricsExporter 的 10 个新指标。
[范围] 独立示例文件，不修改任何现有 CI/CD 脚本——确认逻辑无误后再集成。
[适配] 适用于独立运行的 CI/CD 流水线（GitHub Actions / Jenkins / 自建脚本）。

指标对齐关系（本示例每个埋点都标注了对应的 dashboard 面板）：
  record_ci_pipeline_run(stage)         → Yunshu_ci_pipeline_runs_total      [by(stage)]
  set_ci_pipeline_duration(seconds)     → Yunshu_ci_pipeline_duration_seconds
  set_ci_test_coverage(percent)         → Yunshu_ci_test_coverage_percent
  record_ci_test_failure()              → Yunshu_ci_test_failures_total
  record_ci_build_failure()             → Yunshu_ci_build_failures_total
  set_deployment_status(env, status)    → Yunshu_deployment_status           [{{environment}}]
  set_deployment_duration(env, seconds) → Yunshu_deployment_duration_seconds [{{environment}}]
  record_deployment_failure()           → Yunshu_deployment_failures_total
  record_deployment(status)             → Yunshu_deployment_total            [by(status)]
  record_rollback()                     → Yunshu_rollback_total
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

# CI/CD 推荐用 pushgateway 推送指标（短暂任务无需常驻 HTTP 服务器）
PUSHGATEWAY_URL = "http://monitoring.internal:9091"  # [CHG-2026-0730] 替换为内部监控地址
CI_JOB_NAME = "yunshu-cicd-pipeline"


# ---------------------------------------------------------------------------
# Exporter 实例获取 + 降级保护
# ---------------------------------------------------------------------------
def get_exporter(port: int = 8000) -> Any:
    """获取 PrometheusMetricsExporter 实例。

    [不易] CI/CD 脚本在独立进程中运行，与主应用隔离，不会触发指标重复注册。
    若 prometheus_client 未安装会抛 RuntimeError，CI/CD 脚本应捕获并降级为 no-op，
    避免监控埋点失败阻塞流水线主流程（变易：监控是辅助，不能影响交付）。
    """
    try:
        from agent.monitoring.prometheus import PrometheusMetricsExporter
        return PrometheusMetricsExporter(port=port)
    except RuntimeError as e:
        logger.warning("prometheus_client 不可用，指标埋点降级为 no-op: %s", e)
        return _NullExporter()
    except ImportError as e:
        logger.warning("无法导入 PrometheusMetricsExporter，降级为 no-op: %s", e)
        return _NullExporter()


class _NullExporter:
    """降级空实现：prometheus_client 不可用时的 no-op 占位。

    [简易] 通过 __getattr__ 拦截所有方法调用，无需逐个 stub。
    """

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **kw: None


def push_metrics(exporter: Any, job: str = CI_JOB_NAME) -> None:
    """通过 pushgateway 推送指标（CI/CD 短暂任务推荐方式）。

    [不易] CI/CD 任务运行即退出，Prometheus 无法主动 scrape，
    必须用 pushgateway 推送；推送失败仅记日志，不阻塞流水线。
    """
    try:
        from prometheus_client import REGISTRY, push_to_gateway
        push_to_gateway(PUSHGATEWAY_URL, job=job, registry=REGISTRY)
        logger.info("[metrics] 指标已推送到 pushgateway: %s", PUSHGATEWAY_URL)
    except Exception as e:
        logger.error("[metrics] 推送指标失败（不影响流水线）: %s", e)


# ---------------------------------------------------------------------------
# CI/CD 全流程埋点示例（构建 → 测试 → 部署 → 回滚）
# ---------------------------------------------------------------------------
def run_build_stage(exporter: Any, build_success: bool) -> bool:
    """构建阶段埋点。"""
    print("[CI] === 构建阶段 ===")
    start = time.time()
    # ... 实际构建逻辑（此处省略）...
    duration = time.time() - start

    # [埋点] 记录流水线运行（按 stage 分组，对齐 dashboard by(stage)）
    exporter.record_ci_pipeline_run(stage="build")
    # [埋点] 设置构建耗时（Gauge）
    exporter.set_ci_pipeline_duration(duration)

    if not build_success:
        # [埋点] 记录构建失败
        exporter.record_ci_build_failure()
        print(f"[CI] 构建失败 (耗时 {duration:.1f}s)")
        return False

    print(f"[CI] 构建成功 (耗时 {duration:.1f}s)")
    return True


def run_test_stage(exporter: Any, test_success: bool, coverage_percent: float) -> bool:
    """测试阶段埋点。"""
    print("[CI] === 测试阶段 ===")
    # ... 实际测试逻辑（此处省略）...

    # [埋点] 设置测试覆盖率（Gauge，对齐 dashboard yunshu_ci_test_coverage_percent）
    exporter.set_ci_test_coverage(coverage_percent)
    # [埋点] 记录流水线运行（测试阶段）
    exporter.record_ci_pipeline_run(stage="test")

    if not test_success:
        # [埋点] 记录测试失败
        exporter.record_ci_test_failure()
        print(f"[CI] 测试失败 (覆盖率 {coverage_percent}%)")
        return False

    print(f"[CI] 测试通过 (覆盖率 {coverage_percent}%)")
    return True


def run_deploy_stage(
    exporter: Any,
    environment: str,
    deploy_success: bool,
    duration_seconds: float,
) -> bool:
    """部署阶段埋点。"""
    print(f"[CD] === 部署阶段 (env={environment}) ===")

    # [埋点] 设置部署状态为 Deploying（对齐 dashboard mappings: 1=Deploying）
    exporter.set_deployment_status(environment, 1)
    # [埋点] 记录流水线运行（部署阶段）
    exporter.record_ci_pipeline_run(stage="deploy")
    push_metrics(exporter)  # 推送中间状态，让 dashboard 实时看到 Deploying

    if not deploy_success:
        # [埋点] 记录部署失败
        exporter.record_deployment_failure()
        # [埋点] 设置部署状态为 Failed（3=Failed）
        exporter.set_deployment_status(environment, 3)
        print(f"[CD] 部署失败 (耗时 {duration_seconds:.1f}s)")
        return False

    # [埋点] 记录成功部署（按 status 分组，对齐 dashboard by(status)）
    exporter.record_deployment(status="success")
    # [埋点] 设置部署耗时（Gauge，按 environment 分组）
    exporter.set_deployment_duration(environment, duration_seconds)
    # [埋点] 设置部署状态为 Stable（0=Stable）
    exporter.set_deployment_status(environment, 0)
    print(f"[CD] 部署成功 (耗时 {duration_seconds:.1f}s)")
    return True


def run_rollback(exporter: Any, environment: str) -> None:
    """回滚埋点。"""
    print(f"[CD] === 回滚 (env={environment}) ===")

    # [埋点] 记录回滚
    exporter.record_rollback()
    # [埋点] 设置部署状态为 Rollback（2=Rollback）
    exporter.set_deployment_status(environment, 2)
    # [埋点] 记录回滚部署（status=rollback）
    exporter.record_deployment(status="rollback")
    print(f"[CD] 回滚完成")


# ---------------------------------------------------------------------------
# 主流程：模拟一次完整的 CI/CD 流程
# ---------------------------------------------------------------------------
def main() -> int:
    """CI/CD 流水线主入口（示例）。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    exporter = get_exporter()

    print("=" * 60)
    print("CI/CD Pipeline 开始")
    print("=" * 60)

    # --- CI 阶段 ---
    # 修改这里的 True/False 可模拟不同失败场景
    if not run_build_stage(exporter, build_success=True):
        push_metrics(exporter)
        return 1

    if not run_test_stage(exporter, test_success=True, coverage_percent=87.3):
        push_metrics(exporter)
        return 1

    # --- CD 阶段 ---
    if not run_deploy_stage(
        exporter,
        environment="production",
        deploy_success=True,
        duration_seconds=145.8,
    ):
        # 部署失败 → 触发回滚
        run_rollback(exporter, environment="production")
        push_metrics(exporter)
        return 1

    # 推送所有指标到 pushgateway
    push_metrics(exporter)

    print("=" * 60)
    print("CI/CD Pipeline 完成")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
