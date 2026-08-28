"""五层健康探针：进程资源 / 依赖服务 / LLM工具 / 业务接口 / 语义质量

【不易】探针契约（与 assessor.assess_with_probes 对齐）：
- 每层返回 ProbeResult(layer, score, detail, available)
- available=False 表示该层未采集到真实数据，score 必须为 None
  （禁止假满分：无数据时参与归一化即失真）
- 单层探针失败不允许向上抛异常，降级为 available=False 并记录 detail

数据源（复用既有能力，不重复造轮子）：
- L1: psutil（进程资源）
- L2: configs/*.yaml 核心配置文件可读性
- L3: agent.monitoring.metrics 中 LLM/工具链路错误率 + circuit_breaker 状态
- L4: agent.monitoring.metrics 中业务接口请求/成功率
- L5: agent.feedback 用户反馈满意度

【变易】各探针独立函数，便于单测注入 mock 数据源。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

CONFIGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "configs")
# 核心配置：L2 依赖可读性检查对象（缺任一即认为依赖不可用）
CORE_CONFIGS = ("app.yaml", "paths.yaml")


@dataclass
class ProbeResult:
    """单层探针结果

    Attributes:
        layer: 层标识（l1_process/l2_dependency/l3_llm_tool/l4_business/l5_semantic）
        score: 归一化分数 0.0~1.0；无数据时必须为 None
        detail: 人类可读的采集摘要
        available: 是否采集到真实数据
    """
    layer: str
    score: Optional[float]
    detail: str
    available: bool = True


def _as_dict(result: ProbeResult) -> dict:
    return {
        "score": result.score,
        "available": result.available,
        "detail": result.detail,
    }


def probe_l1_process() -> ProbeResult:
    """L1 进程资源：CPU/内存/线程数/磁盘剩余

    数据缺失（psutil 不可用或采集失败）→ available=False, score=None。
    """
    try:
        import psutil
    except ImportError:
        return ProbeResult("l1_process", None, "psutil 未安装", available=False)
    try:
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None) or 0.0
        threads = len(psutil.Process().threads())
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        mem_pct = vm.percent
        disk_free_pct = disk.free / disk.total * 100
        detail = f"mem={mem_pct:.1f}% cpu={cpu:.1f}% threads={threads} disk_free={disk_free_pct:.1f}%"
        # 评分：内存/CPU 越低分越高；磁盘剩余越少越低分
        score = 1.0
        if mem_pct > 85 or cpu > 90:
            score -= 0.5
        elif mem_pct > 70 or cpu > 75:
            score -= 0.2
        if disk_free_pct < 5:
            score -= 0.3
        elif disk_free_pct < 10:
            score -= 0.1
        return ProbeResult("l1_process", max(0.0, round(score, 2)), detail)
    except Exception as e:  # noqa: BLE001 - 探针失败降级为无数据
        logger.warning("probe_l1_process 采集失败: %s", e)
        return ProbeResult("l1_process", None, f"进程资源采集失败: {type(e).__name__}", available=False)


def probe_l2_dependency() -> ProbeResult:
    """L2 依赖服务：核心配置文件可读性

    全部核心配置可读 → 可用；任一不可读/目录缺失 → available=False。
    """
    readable = 0
    failed = []
    for name in CORE_CONFIGS:
        path = os.path.join(CONFIGS_DIR, name)
        if os.path.isfile(path) and os.access(path, os.R_OK):
            readable += 1
        else:
            failed.append(name)
    total = len(CORE_CONFIGS)
    if failed:
        detail = f"configs 核心文件不可读 {readable}/{total}: {','.join(failed)}"
        return ProbeResult("l2_dependency", None, detail, available=False)
    detail = f"configs 核心文件可读 {readable}/{total}"
    return ProbeResult("l2_dependency", 1.0, detail)


def probe_l3_llm_tool() -> ProbeResult:
    """L3 LLM/工具链路：调用错误率 + 熔断状态

    无调用计数或 metrics 不可用 → available=False, score=None。
    """
    try:
        from agent.monitoring.metrics import get_all_metrics
        all_metrics = get_all_metrics()
    except Exception as e:  # noqa: BLE001
        logger.warning("probe_l3_llm_tool metrics 不可用: %s", e)
        return ProbeResult("l3_llm_tool", None, "无 LLM/工具链路数据（metrics 未初始化）", available=False)

    counters = all_metrics.get("counters", {})
    error_keys = [k for k in counters if "error" in k.lower()]
    total_keys = [k for k in counters if "total" in k.lower() or "count" in k.lower()]
    errors = sum(counters[k] for k in error_keys)
    total = sum(counters[k] for k in total_keys)

    if total <= 0:
        # 兜底检查熔断器状态
        try:
            from agent.circuit_breaker import get_circuit_breakers
            states = get_circuit_breakers()
            if states:
                opened = [name for name, st in states.items() if getattr(st, "state", None) == "open"]
                if opened:
                    return ProbeResult("l3_llm_tool", 0.1, f"熔断器打开: {','.join(opened)}")
                return ProbeResult("l3_llm_tool", 1.0, "熔断器全部关闭")
        except Exception:  # noqa: BLE001
            pass
        return ProbeResult("l3_llm_tool", None, "无 LLM/工具链路数据（未注册熔断器且无调用计数）", available=False)

    err_rate = errors / total
    detail = f"chat_error_rate={err_rate:.1%} (errors={errors} total={total})"
    score = max(0.0, 1.0 - err_rate * 5)
    return ProbeResult("l3_llm_tool", round(score, 2), detail)


def probe_l4_business() -> ProbeResult:
    """L4 业务接口：最近请求成功率

    最近窗口无业务请求 → available=False, score=None。
    """
    try:
        from agent.monitoring.metrics import get_all_metrics
        all_metrics = get_all_metrics()
    except Exception as e:  # noqa: BLE001
        logger.warning("probe_l4_business metrics 不可用: %s", e)
        return ProbeResult("l4_business", None, "最近窗口无业务请求", available=False)

    counters = all_metrics.get("counters", {})
    requests = sum(v for k, v in counters.items() if "request" in k.lower())
    errors = sum(v for k, v in counters.items() if "error" in k.lower() and "request" in k.lower())

    if requests <= 0:
        return ProbeResult("l4_business", None, "最近窗口无业务请求", available=False)

    success_rate = 1.0 - (errors / requests) if requests else 0.0
    detail = f"requests={requests} success_rate={success_rate:.1%}"
    return ProbeResult("l4_business", round(success_rate, 2), detail)


def probe_l5_semantic() -> ProbeResult:
    """L5 语义质量：用户反馈满意度

    近窗口无用户反馈 → available=False, score=None。
    """
    try:
        from agent.feedback import FeedbackManager
        manager = FeedbackManager()
        records = manager.list_feedback(limit=200)
    except Exception as e:  # noqa: BLE001
        logger.warning("probe_l5_semantic feedback 不可用: %s", e)
        return ProbeResult("l5_semantic", None, "近 7 天无用户反馈", available=False)

    if not records:
        return ProbeResult("l5_semantic", None, "近 7 天无用户反馈", available=False)

    total = len(records)
    dislikes = sum(1 for r in records if getattr(r, "feedback_type", None) == "dislike")
    likes = sum(1 for r in records if getattr(r, "feedback_type", None) == "like")
    satisfaction = likes / total if total else 0.0
    detail = f"feedback={total} satisfaction={satisfaction:.1%} dislike={dislikes}"
    return ProbeResult("l5_semantic", round(satisfaction, 2), detail)


# 探针执行顺序（固定，保证 probe_details 键序稳定）
_PROBES = (
    probe_l1_process,
    probe_l2_dependency,
    probe_l3_llm_tool,
    probe_l4_business,
    probe_l5_semantic,
)


def run_all_probes() -> dict:
    """运行全部五层探针，返回 {layer: ProbeResult}

    单层失败不影响其余层；结果供 assessor.assess_with_probes 使用。
    """
    results = {}
    for probe in _PROBES:
        result = probe()
        results[result.layer] = result
        _log_probe(result)
    return results


def _log_probe(result: ProbeResult) -> None:
    """记录单层探针结构化日志（验收标准：module_name=health_probes）

    available=True → info(probe.<layer>.completed)；available=False →
    warning(probe.<layer>.failed)。log_dict 结构化输出供日志聚合消费。
    """
    payload = log_dict({
        "module_name": "health_probes",
        "action": f"probe.{result.layer}.{'completed' if result.available else 'failed'}",
        "available": result.available,
        "score": result.score,
        "detail": result.detail,
    })
    if result.available:
        logger.info(payload)
    else:
        logger.warning(payload)
