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

#: 【2026-11-01】L1 内存判据：改用**可用内存绝对值**（本机 15GB、模型常驻 ⇒ 百分比长期 >85%）
#: 分档（实测 01:20 avail≈1019MB，正好卡在 1GB 线上 ⇒ 硬阈值会随 GC 跳变，故分两档）：
#:   < 512MB  → 扣 0.5（真危险：随时可能 OOM）
#:   < 1024MB → 扣 0.2（偏紧，但可服务）
#:   > 95%    → 再兜底扣 0.2（防 "available 虚高" 的极端情况）
L1_CRITICAL_MEM_MB = 512
L1_LOW_MEM_MB = 1024
L1_EXTREME_MEM_PCT = 95.0

#: 【2026-11-01】L5 语义质量的最小样本：低于它**不评分**（available=False），
#: 否则 1 条 dislike / 2 条反馈就能把整机健康分从 1.0 拽到 0.68（2026-10-31 实测）。
#: 【2026-11-08】样本只数 **like / dislike**（真正的满意度信号），不再数全部反馈。
L5_MIN_FEEDBACK = 5

#: 【2026-11-08】L5 的时间窗（天）。docstring/文案一直写"近 7 天"，而老实现是全量扫描 ——
#: 7 月的 1 条 like 会永远留在分母里，与"近 7 天"名不符实。
L5_WINDOW_DAYS = 7

#: 满意度信号的反馈类型：只有这两种能表达"满意/不满意"。
#: suggestion（开发随记）/ bug 报告不是满意度信号 —— 把它们计入分母就是"没点赞算差评"，
#: 实测：7 条 dev-note 把 L5 从 50% 压到 11.1%，整机健康分凭空掉了 0.1 权重。
L5_SENTIMENT_TYPES = ("like", "dislike")


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
    #: 结构化数值（可选；界面要显示具体数字时用它，别去正则拆 detail 文本）
    metrics: Optional[dict] = None


def _as_dict(result: ProbeResult) -> dict:
    out = {
        "score": result.score,
        "available": result.available,
        "detail": result.detail,
    }
    # 【2026-11-08】只在这层真有结构化数值时才加键 —— 老消费方（storage/前端）
    # 看到的形状与以前逐字节一致，不会因为多一个 null 键而出现假 diff。
    if result.metrics:
        out["metrics"] = dict(result.metrics)
    return out


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
        avail_mb = float(getattr(vm, "available", 0)) / (1024 * 1024)
        disk_free_pct = disk.free / disk.total * 100
        detail = (f"mem={mem_pct:.1f}% avail={avail_mb:.0f}MB cpu={cpu:.1f}%"
                  f" threads={threads} disk_free={disk_free_pct:.1f}%")
        # 【2026-11-08】结构化数值：健康仪表盘概览卡的「CPU 核心数 / 内存可用量 / 磁盘可用」
        # 以前恒为「—（端点未返回）」，因为 /api/health/heartbeat 只给使用率百分比。
        # 这些数 L1 探针本来就算出来了（detail 里也有），以前只拼成文本 ⇒ 前端只能显示 MISSING。
        metrics = {
            "cpu_pct": round(float(cpu), 1),
            "cpu_cores": int(psutil.cpu_count() or 0),
            "mem_pct": round(float(mem_pct), 1),
            "mem_available_mb": round(float(avail_mb), 0),
            "mem_total_mb": round(float(getattr(vm, "total", 0)) / (1024 * 1024), 0),
            "threads": int(threads),
            "disk_free_pct": round(float(disk_free_pct), 1),
            "disk_free_gb": round(float(disk.free) / (1024 ** 3), 1),
        }
        # 评分：**可用内存绝对值** + CPU；磁盘剩余越少越低分。
        # 【2026-11-01】原判据是 mem_pct > 85（百分比）：本机 15GB、模型+浏览器常驻，
        # 91% 是常态 ⇒ 每次巡检都扣 0.5，把整体健康分压到 0.68、三个服务节点一起变黄。
        # 百分比高但**还剩 1GB 以上**并不影响服务；真紧张时按可用内存扣分更贴近事实。
        score = 1.0
        if avail_mb < L1_CRITICAL_MEM_MB or cpu > 90:
            score -= 0.5
        elif avail_mb < L1_LOW_MEM_MB or cpu > 75 or mem_pct > L1_EXTREME_MEM_PCT:
            score -= 0.2
        if disk_free_pct < 5:
            score -= 0.3
        elif disk_free_pct < 10:
            score -= 0.1
        return ProbeResult("l1_process", max(0.0, round(score, 2)), detail, metrics=metrics)
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

    # 【2026-11-08】把前缀缓存命中率带成结构化数值：健康仪表盘要一张「LLM 缓存命中」卡。
    # 口径只认 API **明确上报过**缓存字段的调用（cache_reported），否则"没上报"会被算成
    # "未命中"而把命中率拉低（见 LLMMonitor.extract_usage 的注释）。
    cache_metrics = None
    try:
        from agent.llm_monitor import get_monitor
        stats = get_monitor().get_stats()
        ratio = stats.get("cache_hit_ratio")
        if ratio is not None:
            cache_metrics = {
                "cache_hit_ratio": round(float(ratio), 4),
                "cache_hit_tokens": int(stats.get("cache_hit_tokens") or 0),
                "cache_miss_tokens": int(stats.get("cache_miss_tokens") or 0),
                "cache_reported_calls": int(stats.get("cache_reported_calls") or 0),
                "llm_calls": int(stats.get("total") or 0),
            }
    except Exception as _ce:  # noqa: BLE001 缓存读数失败不影响 L3 的既有判定
        logger.debug("probe_l3_llm_tool 缓存读数不可用: %s", _ce)

    if total <= 0:
        # 兜底检查熔断器状态
        try:
            from agent.circuit_breaker import get_circuit_breakers
            states = get_circuit_breakers()
            if states:
                opened = [name for name, st in states.items() if getattr(st, "state", None) == "open"]
                if opened:
                    return ProbeResult("l3_llm_tool", 0.1, f"熔断器打开: {','.join(opened)}",
                                       metrics=cache_metrics)
                return ProbeResult("l3_llm_tool", 1.0, "熔断器全部关闭", metrics=cache_metrics)
        except Exception:  # noqa: BLE001
            pass
        return ProbeResult("l3_llm_tool", None, "无 LLM/工具链路数据（未注册熔断器且无调用计数）",
                           available=False, metrics=cache_metrics)

    err_rate = errors / total
    detail = f"chat_error_rate={err_rate:.1%} (errors={errors} total={total})"
    if cache_metrics:
        detail += " cache_hit=%.1f%%" % (cache_metrics["cache_hit_ratio"] * 100)
    score = max(0.0, 1.0 - err_rate * 5)
    return ProbeResult("l3_llm_tool", round(score, 2), detail, metrics=cache_metrics)


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


def _feedback_age_days(record) -> Optional[float]:
    """反馈距今多少天；**取不到时间就返回 None**（只可能是测试替身/老数据）。

    `created_at` 在真库里是 float 字符串（如 `1793880805.0841296`），也可能是 datetime，
    这里统一成"距今天数"；解析不出来时**不算过期**（宁可多算，也不要因为格式不认识就丢样本）。
    """
    raw = getattr(record, "created_at", None)
    if raw in (None, ""):
        return None
    try:
        import time as _time

        if isinstance(raw, (int, float)):
            ts = float(raw)
        else:
            text = str(raw).strip()
            try:
                ts = float(text)
            except ValueError:
                from datetime import datetime

                ts = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        return max(0.0, (_time.time() - ts) / 86400.0)
    except Exception:  # noqa: BLE001 解析失败 ⇒ 当作"时间未知"
        return None


def probe_l5_semantic() -> ProbeResult:
    """L5 语义质量：**近 7 天**用户反馈里的满意度（like / (like + dislike)）。

    【口径（2026-11-08 修）】
    - 只看近 `L5_WINDOW_DAYS` 天（与文案一致；老实现是全量，7 月的老反馈一直留在分母）；
    - 分母只含 like / dislike：`suggestion`（开发随记）`bug` 等**不是满意度信号**，
      计入分母等于"没点赞就算差评"（实测 7 条随记把 50% 压成 11.1%）；
    - 样本不足 `L5_MIN_FEEDBACK` 不评分；一条 like/dislike 都没有也如实报"无数据"。
    """
    try:
        from agent.feedback import FeedbackManager
        manager = FeedbackManager()
        records = manager.list_feedback(limit=200)
    except Exception as e:  # noqa: BLE001
        logger.warning("probe_l5_semantic feedback 不可用: %s", e)
        return ProbeResult("l5_semantic", None, "反馈库不可用（详见服务日志）", available=False)

    if not records:
        return ProbeResult("l5_semantic", None, "近 7 天无用户反馈", available=False)

    recent, others, stale = [], 0, 0
    for r in records:
        age = _feedback_age_days(r)
        if age is not None and age > L5_WINDOW_DAYS:
            stale += 1
            continue
        if getattr(r, "feedback_type", None) in L5_SENTIMENT_TYPES:
            recent.append(r)
        else:
            others += 1

    likes = sum(1 for r in recent if getattr(r, "feedback_type", None) == "like")
    dislikes = sum(1 for r in recent if getattr(r, "feedback_type", None) == "dislike")
    total = likes + dislikes
    extra = (f"（另有 {others} 条建议/随记不计入满意度"
             + (f"，{stale} 条超出 {L5_WINDOW_DAYS} 天窗口" if stale else "") + "）")

    if total == 0:
        return ProbeResult(
            "l5_semantic", None,
            f"近 {L5_WINDOW_DAYS} 天没有 like/dislike 反馈{extra}",
            available=False,
            metrics={"likes": 0, "dislikes": 0, "suggestions": others, "stale": stale},
        )
    if total < L5_MIN_FEEDBACK:
        return ProbeResult(
            "l5_semantic", None,
            f"样本不足（近 {L5_WINDOW_DAYS} 天 like/dislike 共 {total} 条，需 ≥{L5_MIN_FEEDBACK} 条才评分）{extra}",
            available=False,
            metrics={"likes": likes, "dislikes": dislikes,
                     "suggestions": others, "stale": stale},
        )
    satisfaction = likes / total
    detail = (f"likes={likes} dislikes={dislikes} satisfaction={satisfaction:.1%}{extra}")
    return ProbeResult("l5_semantic", round(satisfaction, 2), detail,
                       metrics={"likes": likes, "dislikes": dislikes,
                                "suggestions": others, "stale": stale})


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
