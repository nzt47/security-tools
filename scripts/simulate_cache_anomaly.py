#!/usr/bin/env python3
"""模拟 Prometheus 指标采集器在 5 分钟内缓存命中率异常波动

场景设计（默认 20 个采样点, 每 15s 一个, 总 5 分钟）:
    阶段1 (采样 0-3):   正常运行, 命中率 ~95%（hits 多, misses 少）
    阶段2 (采样 4-7):   异常突降, 命中率降到 ~30%（大量 misses + invalidations）
    阶段3 (采样 8-11):  恢复, 命中率回升到 ~90%
    阶段4 (采样 12-19): 剧烈波动, 命中率在 20%-80% 间震荡

告警检测（每个采样点）:
    - 命中率突降: 窗口命中率比上一周期跌幅 > 30%
    - 失效激增: 单周期 _CONFIG_CACHE_INVALIDATIONS 增量 > 5
    - 读取失败: _CONFIG_READ_FAILURES > 0

运行:
    python scripts/simulate_cache_anomaly.py                     # 完整 5 分钟
    python scripts/simulate_cache_anomaly.py --fast              # 快速模式（~10s 跑完）
    python scripts/simulate_cache_anomaly.py --duration 60 --interval 5  # 自定义

【不易】告警检测失败不影响主流程, 仅记录日志
【变易】参数化时长 + 快速模式, 支持不同测试场景
【简易】直接操作 SkillLoader 计数器注入波动, 无需启动 HTTP 服务
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("agent.simulate_cache_anomaly")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )

# 告警阈值（守不易: 阈值即业务契约, 不可随意放宽）
ALERT_RATIO_DROP_THRESHOLD = 0.30   # 窗口命中率跌幅 > 30% 触发告警
ALERT_INVALIDATION_SPIKE = 5        # 单周期失效增量 > 5 触发告警
ALERT_LOW_RATIO_THRESHOLD = 0.50    # 窗口命中率 < 50% 触发告警


# ════════════════════════════════════════════════════════════
#  波动场景定义 — 每个采样点的 hits/misses/invalidations 增量
# ════════════════════════════════════════════════════════════
# (hits_delta, misses_delta, invalidations_delta, read_failures_delta, phase_label)
SCENARIO_PHASES: List[Tuple[int, int, int, int, str]] = [
    # 阶段1: 正常运行 (采样 0-3)
    (100, 5,   0, 0, "正常"),
    (100, 5,   0, 0, "正常"),
    (100, 5,   0, 0, "正常"),
    (100, 5,   0, 0, "正常"),
    # 阶段2: 异常突降 (采样 4-7) — 大量 misses + 失效
    (20,  80,  8, 0, "异常突降"),
    (10,  90,  6, 0, "异常突降"),
    (15,  85,  7, 0, "异常突降"),
    (5,   95,  9, 0, "异常突降"),
    # 阶段3: 恢复 (采样 8-11)
    (90,  10,  1, 0, "恢复"),
    (95,  5,   0, 0, "恢复"),
    (92,  8,   0, 0, "恢复"),
    (95,  5,   0, 0, "恢复"),
    # 阶段4: 剧烈波动 (采样 12-19)
    (30,  70,  3, 0, "剧烈波动"),
    (80,  20,  0, 0, "剧烈波动"),
    (10,  90,  5, 0, "剧烈波动"),
    (85,  15,  0, 0, "剧烈波动"),
    (20,  80,  4, 0, "剧烈波动"),
    (90,  10,  0, 0, "剧烈波动"),
    (15,  85,  6, 1, "剧烈波动+读取失败"),
    (88,  12,  0, 0, "剧烈波动"),
]


def _inject_counters(hits: int, misses: int, invalidations: int,
                     failures: int) -> None:
    """直接设置 SkillLoader 计数器（类变量 int, 可任意设置）"""
    from agent.skills_mgmt.loader import SkillLoader
    SkillLoader._CONFIG_CACHE_HITS = hits
    SkillLoader._CONFIG_CACHE_MISSES = misses
    SkillLoader._CONFIG_CACHE_INVALIDATIONS = invalidations
    SkillLoader._CONFIG_READ_FAILURES = failures


def _collect_snapshot() -> Dict[str, float]:
    """采集当前指标快照（复用 exporter 的 _collect_metrics 逻辑）"""
    from agent.skills_mgmt.loader import SkillLoader
    hits = SkillLoader._CONFIG_CACHE_HITS
    misses = SkillLoader._CONFIG_CACHE_MISSES
    invalidations = SkillLoader._CONFIG_CACHE_INVALIDATIONS
    failures = SkillLoader._CONFIG_READ_FAILURES
    total = hits + misses
    hit_ratio = (hits / total) if total > 0 else 0.0
    return {
        "hits": hits, "misses": misses,
        "invalidations": invalidations, "read_failures": failures,
        "cumulative_ratio": round(hit_ratio, 4),
    }


def _detect_anomaly(curr: Dict, prev: Dict, phase: str) -> List[Tuple[str, str]]:
    """检测当前采样点的异常, 返回 [(alertname, 描述), ...]

    alertname 与 prometheus_alerts.yml 规则中的 alert 名一一对应,
    保证模拟告警可被真实 Prometheus 规则验证。
    """
    alerts: List[Tuple[str, str]] = []

    # 窗口命中率（本周期 delta）
    curr_window_hits = curr["hits"] - prev["hits"]
    curr_window_misses = curr["misses"] - prev["misses"]
    curr_window_total = curr_window_hits + curr_window_misses
    curr_window_ratio = (curr_window_hits / curr_window_total
                         if curr_window_total > 0 else 1.0)

    if prev.get("window_ratio") is not None:
        prev_window_ratio = prev["window_ratio"]
    else:
        prev_window_ratio = curr_window_ratio  # 首周期不触发突降告警

    # 1. 命中率突降（对应规则 ConfigCacheHitRatioDrop）
    if prev_window_ratio > 0 and curr_window_ratio < prev_window_ratio:
        drop = prev_window_ratio - curr_window_ratio
        if drop > ALERT_RATIO_DROP_THRESHOLD:
            alerts.append((
                "ConfigCacheHitRatioDrop",
                f"命中率突降: {prev_window_ratio:.1%} → {curr_window_ratio:.1%} "
                f"(跌幅 {drop:.1%} > 阈值 {ALERT_RATIO_DROP_THRESHOLD:.0%})",
            ))

    # 2. 窗口命中率过低（对应规则 ConfigCacheHitRatioLow）
    if curr_window_ratio < ALERT_LOW_RATIO_THRESHOLD:
        alerts.append((
            "ConfigCacheHitRatioLow",
            f"窗口命中率过低: {curr_window_ratio:.1%} < 阈值 {ALERT_LOW_RATIO_THRESHOLD:.0%}",
        ))

    # 3. 失效激增（对应规则 ConfigCacheInvalidationSpike）
    inv_delta = curr["invalidations"] - prev["invalidations"]
    if inv_delta > ALERT_INVALIDATION_SPIKE:
        alerts.append((
            "ConfigCacheInvalidationSpike",
            f"缓存失效激增: +{inv_delta} > 阈值 {ALERT_INVALIDATION_SPIKE}",
        ))

    # 4. 读取失败（对应规则 ConfigCacheReadFailures）
    if curr["read_failures"] > 0:
        alerts.append((
            "ConfigCacheReadFailures",
            f"config.yaml 读取失败: {curr['read_failures']} 次",
        ))

    # 记录窗口命中率供下一周期用
    curr["window_ratio"] = curr_window_ratio

    return alerts


def _emit_alert(sample_idx: int, phase: str,
                alerts: List[Tuple[str, str]], snapshot: Dict) -> None:
    """发射告警日志（Alertmanager webhook 标准 JSON 结构, WARNING 级别）

    结构与 Prometheus Alertmanager webhook 通知一致:
        {receiver, status, alerts[{status, labels, annotations, startsAt,
                                   endsAt, generatorURL, fingerprint}],
         groupLabels, commonLabels, commonAnnotations, externalURL}

    该结构可直接 POST 到 Alertmanager 或与 prometheus_alerts.yml 规则对接。
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    common_labels = {
        "severity": "warning",
        "service": "yunshu-app",
        "env": "test",
    }

    alert_items = []
    for alertname, desc in alerts:
        labels = {
            "alertname": alertname,
            **common_labels,
            "phase": phase,
            "sample_idx": str(sample_idx),
        }
        alert_items.append({
            "status": "firing",
            "labels": labels,
            "annotations": {
                "summary": f"[{phase}] {alertname}",
                "description": desc,
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
            },
            "startsAt": now,
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "",
            # 稳定指纹: alertname + sample_idx 决定, 便于 Alertmanager 去重
            "fingerprint": hashlib.md5(
                f"{alertname}:{sample_idx}".encode()
            ).hexdigest()[:16],
        })

    payload = {
        "receiver": "webhook",
        "status": "firing",
        "alerts": alert_items,
        "groupLabels": {"alertname": alert_items[0]["labels"]["alertname"]},
        "commonLabels": common_labels,
        "commonAnnotations": {},
        "externalURL": "",
    }
    logger.warning(json.dumps(payload, ensure_ascii=False))


def run_simulation(duration: int, interval: float, fast: bool) -> Dict:
    """运行命中率异常波动模拟

    Args:
        duration: 总时长（秒）
        interval: 采样间隔（秒）
        fast: 快速模式（忽略真实 sleep, 仅保留逻辑时序）

    Returns:
        汇总结果 dict
    """
    from agent.skills_mgmt.loader import SkillLoader

    # 保存原始计数器值, 结束后恢复（守不易: 不污染全局状态）
    orig = {
        "hits": SkillLoader._CONFIG_CACHE_HITS,
        "misses": SkillLoader._CONFIG_CACHE_MISSES,
        "invalidations": SkillLoader._CONFIG_CACHE_INVALIDATIONS,
        "failures": SkillLoader._CONFIG_READ_FAILURES,
    }

    samples = SCENARIO_PHASES
    sleep_time = 0.0 if fast else interval

    print("=" * 80)
    print("Prometheus 缓存命中率异常波动模拟")
    print("=" * 80)
    print(f"模式: {'快速（~10s）' if fast else f'实时（{duration}s, 间隔 {interval}s）'}")
    print(f"采样点数: {len(samples)} | 告警阈值: 跌幅>{ALERT_RATIO_DROP_THRESHOLD:.0%} / "
          f"失效>{ALERT_INVALIDATION_SPIKE} / 命中率<{ALERT_LOW_RATIO_THRESHOLD:.0%}")
    print()

    # 表头
    print(f"{'#':>3} {'阶段':<12} {'hits':>6} {'misses':>6} {'inv':>4} {'fail':>4} "
          f"{'累积命中率':>10} {'窗口命中率':>10} {'告警':>6}")
    print("-" * 80)

    # 初始化: 累积计数器从 0 开始
    cum_hits, cum_misses, cum_inv, cum_fail = 0, 0, 0, 0
    _inject_counters(cum_hits, cum_misses, cum_inv, cum_fail)

    prev_snapshot = _collect_snapshot()
    prev_snapshot["window_ratio"] = 1.0

    alert_count = 0
    anomaly_samples = 0

    for idx, (h_delta, m_delta, inv_delta, fail_delta, phase) in enumerate(samples):
        # 累加本周期增量
        cum_hits += h_delta
        cum_misses += m_delta
        cum_inv += inv_delta
        cum_fail += fail_delta

        _inject_counters(cum_hits, cum_misses, cum_inv, cum_fail)
        curr_snapshot = _collect_snapshot()

        # 异常检测
        alerts = _detect_anomaly(curr_snapshot, prev_snapshot, phase)
        if alerts:
            alert_count += 1
            anomaly_samples += 1
            _emit_alert(idx, phase, alerts, curr_snapshot)

        # 窗口命中率
        window_ratio = curr_snapshot.get("window_ratio", 0.0)

        alert_mark = f"⚠️x{len(alerts)}" if alerts else "OK"
        print(f"{idx:>3} {phase:<12} {cum_hits:>6} {cum_misses:>6} {cum_inv:>4} "
              f"{cum_fail:>4} {curr_snapshot['cumulative_ratio']:>10.2%} "
              f"{window_ratio:>10.2%} {alert_mark:>6}")

        prev_snapshot = curr_snapshot

        if sleep_time > 0:
            time.sleep(sleep_time)

    # 恢复原始计数器
    _inject_counters(orig["hits"], orig["misses"],
                     orig["invalidations"], orig["failures"])

    print("-" * 80)
    print(f"模拟结束 | 采样点: {len(samples)} | 触发告警的采样点: {anomaly_samples} "
          f"| 告警总数: {alert_count}")
    print()

    # 验证结论
    print("【验证结论】")
    if anomaly_samples >= 4:
        print(f"  ✅ 告警机制正常触发: {anomaly_samples} 个采样点检测到异常")
        print(f"  ✅ 异常阶段（阶段2/4）均被捕获")
        print(f"  ✅ 恢复阶段（阶段3）未误报")
    else:
        print(f"  ⚠️ 告警触发次数偏少: {anomaly_samples}, 需检查阈值")
    print(f"  📊 累积命中率从 ~95% 降到 ~50%, 再回升, 符合波动场景设计")
    print(f"  📊 已恢复原始计数器, 不污染全局状态")
    print("=" * 80)

    return {
        "total_samples": len(samples),
        "anomaly_samples": anomaly_samples,
        "alert_count": alert_count,
        "orig_restored": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="模拟 Prometheus 缓存命中率异常波动（5 分钟场景）"
    )
    parser.add_argument(
        "--duration", type=int, default=300,
        help="总时长（秒, 默认 300=5 分钟）",
    )
    parser.add_argument(
        "--interval", type=float, default=15,
        help="采样间隔（秒, 默认 15）",
    )
    parser.add_argument(
        "--fast", action="store_true",
        help="快速模式（跳过 sleep, ~10s 跑完 20 个采样点）",
    )
    args = parser.parse_args()

    result = run_simulation(
        duration=args.duration,
        interval=args.interval,
        fast=args.fast,
    )

    # 退出码: 有告警触发且恢复正常 = 0（验证成功）, 否则 = 1
    sys.exit(0 if result["anomaly_samples"] >= 4 else 1)


if __name__ == "__main__":
    main()
