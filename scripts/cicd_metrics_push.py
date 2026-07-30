"""
CI/CD 指标推送脚本（供 GitHub Actions / Jenkins 各阶段调用）

[用途] 在 CI/CD 流水线的 build / test / deploy / rollback 阶段调用，
       将 PrometheusMetricsExporter 的指标推送到 pushgateway。
[调用] python scripts/cicd_metrics_push.py --stage build --success
       python scripts/cicd_metrics_push.py --stage test --success --coverage 87.3
       python scripts/cicd_metrics_push.py --stage deploy --env production --success --duration 145.8
       python scripts/cicd_metrics_push.py --stage rollback --env production
[不易] 推送失败仅记日志，不阻塞流水线（监控是辅助，不能影响交付）。
[变易] 通过 CLI 参数区分阶段，复用同一套指标定义。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any

# [修复 CHG-2026-0731] 注入项目根目录到 sys.path，确保 agent 包可导入
# 脚本从 scripts/ 目录运行时，Python 仅把 scripts/ 加入 sys.path[0]，
# 项目根目录（agent 包所在位置）不在其中，导致 PrometheusMetricsExporter
# 无法实例化，Yunshu_ 自定义指标不会注册到 REGISTRY，推送的仅是默认指标
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logger = logging.getLogger(__name__)

PUSHGATEWAY_URL = os.environ.get("PUSHGATEWAY_URL", "http://monitoring.internal:9091")
# [变易] 推送超时和重试次数支持环境变量配置
# prometheus_client 默认 timeout=30s 太长会拖慢 CI，改为 10s
# [不易] 环境变量解析容错：非数字值降级为默认值，避免误配导致脚本崩溃
try:
    PUSH_TIMEOUT = float(os.environ.get("PUSH_TIMEOUT", "10"))
except (ValueError, TypeError):
    PUSH_TIMEOUT = 10.0
try:
    PUSH_MAX_RETRIES = int(os.environ.get("PUSH_MAX_RETRIES", "1"))
except (ValueError, TypeError):
    PUSH_MAX_RETRIES = 1


def get_exporter() -> Any:
    """获取 exporter 实例，prometheus_client 不可用时降级为 no-op。

    [不易] 捕获所有异常而非仅 RuntimeError/ImportError：PrometheusMetricsExporter
           初始化时可能抛 ValueError（重复指标注册）等异常，必须降级不能崩溃。
    """
    try:
        from agent.monitoring.prometheus import PrometheusMetricsExporter
        return PrometheusMetricsExporter()
    except Exception as e:
        logger.warning("PrometheusMetricsExporter 不可用，降级为 no-op → error_type=%s, error=%s",
                       type(e).__name__, e)
        return _NullExporter()


class _NullExporter:
    """降级空实现：所有方法调用均为 no-op。"""
    def __getattr__(self, name: str) -> Any:
        return lambda *a, **kw: None


class _SimpleRetryPolicy:
    """简易重试策略：RetryPolicy 不可用时的降级实现。

    [不易] 项目硬约束要求重试必须用统一 RetryPolicy 类，
           但 CI 环境可能无法导入 agent.error_handler，此为降级兜底。
    [简易] 仅支持固定延迟 + OSError 重试，接口与 RetryPolicy 一致。
    """

    def __init__(self, max_retries: int, delay: float):
        self.max_retries = max_retries
        self.delay = delay

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """仅对网络瞬时错误（OSError 子类）重试，且未超 max_retries。"""
        if attempt >= self.max_retries:
            return False
        # URLError / ConnectionError / TimeoutError 均为 OSError 子类
        return isinstance(exception, OSError)

    def calculate_delay(self, attempt: int) -> float:
        return self.delay


def _get_retry_policy() -> Any:
    """获取重试策略。[不易] 优先用项目统一 RetryPolicy，不可用时降级为内置简易策略。"""
    try:
        from agent.error_handler import RetryPolicy
        return RetryPolicy(
            max_retries=PUSH_MAX_RETRIES,
            initial_delay=1.0,
            strategy="fixed",
            retryable_exceptions=(OSError,),  # 涵盖 URLError/ConnectionError/Timeout
        )
    except Exception as e:
        logger.debug("[metrics] RetryPolicy 不可用，降级为内置简易重试: %s", e)
        return _SimpleRetryPolicy(max_retries=PUSH_MAX_RETRIES, delay=1.0)


def push(job: str) -> None:
    """推送指标到 pushgateway，失败不抛异常，支持重试。

    [不易] 推送失败仅记日志，不阻塞流水线（监控是辅助，不能影响交付）。
    [变易] 通过 RetryPolicy 对瞬时网络错误重试，max_retries/timeout 可配置。
    [修复 CHG-2026-0730] 用 pushadd_to_gateway 代替 push_to_gateway：
    - push_to_gateway 是 DELETE+PUT，会重置 Counter（每次推送清空同 job 旧值）
    - pushadd_to_gateway 只 PUT 不 DELETE，保留其他指标的累计值
    - 通过 grouping_key={run_id, ci_job} 区分每次 CI 运行和并行 job，避免覆盖
    """
    # [排查日志] 推送开始：记录目标地址和 job 名，便于追踪推送流程
    logger.info("[metrics] 推送开始 → url=%s, job=%s", PUSHGATEWAY_URL, job)
    _push_start = time.time()  # [DEBUG] 推送开始时间戳，用于计算耗时
    try:
        from prometheus_client import REGISTRY, pushadd_to_gateway
    except ImportError as e:
        logger.warning("[metrics] prometheus_client 不可用，跳过推送: %s", e)
        return

    # [不易] run_id 区分每次 CI 运行，ci_job 区分同一运行内的并行 job
    # [修复 CHG-2026-0731] 加入 ci_job 避免同 run_id 的并行 job（如 stress-test
    # 与 integration-test 都用 --stage test）在 pushgateway 中互相覆盖
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    github_job = os.environ.get("GITHUB_JOB", "local")
    grouping_key = {"run_id": run_id, "ci_job": github_job}
    # [排查日志] 推送参数：记录 run_id/ci_job/grouping_key，便于排查覆盖问题
    logger.info("[metrics] 推送参数 → run_id=%s, ci_job=%s, grouping_key=%s",
                run_id, github_job, grouping_key)
    # [DEBUG 日志] 记录 registry 指标数，便于排查指标注册问题
    try:
        metric_count = len(REGISTRY._names_to_collectors)
    except Exception:
        metric_count = -1
    logger.debug("[metrics] registry 指标数: %d", metric_count)

    # [变易] 重试机制：仅对网络瞬时错误重试，避免 pushgateway 抖动丢指标
    retry_policy = _get_retry_policy()
    max_attempts = (retry_policy.max_retries + 1) if retry_policy else 1
    last_exception: Exception | None = None

    for attempt in range(max_attempts):
        try:
            pushadd_to_gateway(
                PUSHGATEWAY_URL, job=job, registry=REGISTRY,
                grouping_key=grouping_key, timeout=PUSH_TIMEOUT,
            )
            # [DEBUG 日志] 记录推送耗时和尝试次数
            logger.debug("[metrics] 推送耗时: %.3fs (尝试 %d/%d)",
                         time.time() - _push_start, attempt + 1, max_attempts)
            # [排查日志] 推送成功：记录完整上下文（含尝试次数，便于排查重试场景）
            if attempt > 0:
                logger.info("[metrics] 推送成功（经 %d 次重试） → url=%s, job=%s, run_id=%s",
                            attempt, PUSHGATEWAY_URL, job, run_id)
            else:
                logger.info("[metrics] 推送成功 → url=%s, job=%s, run_id=%s",
                            PUSHGATEWAY_URL, job, run_id)
            return
        except Exception as e:
            last_exception = e
            if retry_policy and retry_policy.should_retry(e, attempt):
                delay = retry_policy.calculate_delay(attempt)
                logger.warning(
                    "[metrics] 推送失败，准备重试 → 尝试 %d/%d, 延迟 %.1fs, error_type=%s",
                    attempt + 1, max_attempts, delay, type(e).__name__)
                time.sleep(delay)
            else:
                break  # 不可重试异常或重试耗尽

    # [DEBUG 日志] 记录失败时的推送耗时
    logger.debug("[metrics] 推送耗时（失败）: %.3fs", time.time() - _push_start)
    # [排查日志] 推送失败：记录异常类型、消息和完整上下文，便于定位问题
    logger.error("[metrics] 推送失败（不影响流水线） → url=%s, job=%s, error_type=%s, error=%s",
                 PUSHGATEWAY_URL, job,
                 type(last_exception).__name__ if last_exception else "Unknown",
                 last_exception)


def record_stage(exporter: Any, stage: str, success: bool, env: str,
                 coverage: float | None, duration: float | None) -> None:
    """根据阶段调用对应的埋点方法。"""
    # [DEBUG 日志] 记录埋点参数，便于排查埋点是否正确执行
    logger.debug("[metrics] 埋点执行 → stage=%s, success=%s, env=%s, coverage=%s, duration=%s",
                 stage, success, env, coverage, duration)
    # [不易] 每个阶段的埋点严格对齐 dashboard PromQL 标签
    exporter.record_ci_pipeline_run(stage=stage)

    if stage == "build":
        if duration is not None:
            exporter.set_ci_pipeline_duration(duration)
        if not success:
            exporter.record_ci_build_failure()

    elif stage == "test":
        if coverage is not None:
            exporter.set_ci_test_coverage(coverage)
        if not success:
            exporter.record_ci_test_failure()

    elif stage == "deploy":
        # 部署状态语义：0=Stable, 1=Deploying, 2=Rollback, 3=Failed
        if not success:
            exporter.record_deployment_failure()
            exporter.set_deployment_status(env, 3)  # Failed
        else:
            exporter.record_deployment(status="success")
            if duration is not None:
                exporter.set_deployment_duration(env, duration)
            exporter.set_deployment_status(env, 0)  # Stable

    elif stage == "rollback":
        exporter.record_rollback()
        exporter.set_deployment_status(env, 2)  # Rollback
        exporter.record_deployment(status="rollback")


def main() -> int:
    parser = argparse.ArgumentParser(description="CI/CD 指标推送")
    parser.add_argument("--stage", required=True,
                        choices=["build", "test", "deploy", "rollback"],
                        help="CI/CD 阶段")
    parser.add_argument("--env", default="production",
                        help="部署环境（默认 production）")
    parser.add_argument("--success", action="store_true",
                        help="本阶段是否成功（不传则视为失败）")
    parser.add_argument("--coverage", type=float, default=None,
                        help="测试覆盖率（test 阶段用）")
    parser.add_argument("--duration", type=float, default=None,
                        help="阶段耗时秒数（build/deploy 阶段用）")
    args = parser.parse_args()

    # [不易] 日志级别支持运行时动态配置
    # LOG_LEVEL 有效值：DEBUG/INFO/WARNING/ERROR/CRITICAL，无效值降级为 INFO
    _log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    _log_level = getattr(logging, _log_level_name, logging.INFO)
    if not isinstance(_log_level, int):
        _log_level = logging.INFO
    logging.basicConfig(level=_log_level, format="%(message)s")

    exporter = get_exporter()
    # [不易] 埋点失败不阻断推送：record_stage 异常被隔离，确保 push 仍执行
    try:
        record_stage(exporter, args.stage, args.success, args.env,
                     args.coverage, args.duration)
    except Exception as e:
        logger.warning("[metrics] 埋点失败（不影响推送） → stage=%s, error_type=%s, error=%s",
                       args.stage, type(e).__name__, e)
    push(f"ci-cd-{args.stage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
