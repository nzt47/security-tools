#!/usr/bin/env python3
"""观察期基线采集脚本：yunshu_voice_entry_unassigned_total 生产基线。

依据 alert_threshold_calibration_plan.md §二-A（观察期）：
采集 VoiceEntryUnassignedHigh 告警指标的基线分布，用于校准告警阈值。

采集项：
1. 观察期异常总量（--window 窗口内 sum(increase)）
2. 按天异常分布（判断噪声水平）
3. 10 分钟窗口最大增量（校验当前阈值 >3 是否合理）
4. 异常率（异常 / 请求总量，请求指标路径标签需人工核实）

用法：
    # 默认查询最近 7 天（Prometheus 地址 localhost:9090）
    python scripts/collect_voice_entry_baseline.py
    # 指定 Prometheus 地址与观察窗口（如 14 天）
    python scripts/collect_voice_entry_baseline.py --prometheus-url http://prometheus:9090 --window 14d
    # JSON 输出（供 CI/告警系统解析）
    python scripts/collect_voice_entry_baseline.py --json

退出码：0 正常 / 1 阈值超限（10 分钟窗口峰值 > 当前阈值 3）/ 2 运行或查询错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# 当前告警阈值（与 alert_rules.yml VoiceEntryUnassignedHigh 保持一致）
CURRENT_THRESHOLD = 3

# 请求总量指标（异常率分母）。需人工核实 path 标签格式是否包含 /api/voice/listen
REQUEST_METRIC = 'yunshu_http_request_total'


def prom_query(prom_url: str, query: str, timeout: float = 30.0) -> List[Dict[str, Any]]:
    """执行 Prometheus /api/v1/query 查询，返回 data.result 列表。

    【边界显性化】查询失败抛出 RuntimeError（含状态码/错误信息），不静默返回空。
    """
    params = urllib.parse.urlencode({"query": query})
    url = f"{prom_url}/api/v1/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Prometheus HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Prometheus 连接失败: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Prometheus 查询异常: {e}") from e

    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus 返回错误: {body.get('error', body)}")
    return body.get("data", {}).get("result", [])


def query_scalar(prom_url: str, query: str) -> Optional[float]:
    """执行查询并取第一个标量值（无数据返回 None）。"""
    results = prom_query(prom_url, query)
    for r in results:
        try:
            return float(r["value"][1])
        except (KeyError, IndexError, ValueError):
            continue
    return None


def collect(prom_url: str, window: str, interval_min: int = 10) -> Dict[str, Any]:
    """采集观察期基线数据。"""
    report: Dict[str, Any] = {
        "collected_at": time.time(),
        "prometheus_url": prom_url,
        "window": window,
        "interval_min": interval_min,
        "current_threshold": CURRENT_THRESHOLD,
        "items": {},
    }

    # 1. 观察期异常总量
    total = query_scalar(prom_url, f"sum(increase(yunshu_voice_entry_unassigned_total[{window}]))")
    report["items"]["total_exceptions"] = total if total is not None else 0.0

    # 2. 按天异常分布（取最近 7 天逐日增量）
    daily = prom_query(
        prom_url,
        "sum(increase(yunshu_voice_entry_unassigned_total[24h])) "
        "and on() (time() - timestamp(yunshu_voice_entry_unassigned_total) < 7*24*3600)",
    )
    # PromQL 无法直接按天聚合历史窗口，改用递增累积近似：逐日查询
    day_counts: List[Dict[str, float]] = []
    for i in range(1, 8):
        offset = f"{i}d"
        val = query_scalar(
            prom_url,
            f"sum(increase(yunshu_voice_entry_unassigned_total[24h] offset {offset}))",
        )
        if val is not None:
            day_counts.append({"day_offset": offset, "count": val})
    report["items"]["daily_distribution"] = day_counts

    # 3. 10 分钟窗口最大增量（对齐告警窗口，校验阈值合理性）
    peak = query_scalar(
        prom_url,
        f"max_over_time(increase(yunshu_voice_entry_unassigned_total[{interval_min}m])[{window}])",
    )
    report["items"]["peak_{m}min_window".format(m=interval_min)] = peak if peak is not None else 0.0

    # 4. 异常率（异常 / 请求总量）。请求指标不可用时降级为 None 并标记需人工核实
    req_total = query_scalar(prom_url, f"sum(increase({REQUEST_METRIC}[{window}]))")
    if req_total is not None and req_total > 0:
        report["items"]["exception_rate"] = round(report["items"]["total_exceptions"] / req_total, 6)
        report["items"]["request_total"] = req_total
    else:
        report["items"]["exception_rate"] = None
        report["items"]["request_total"] = req_total
        report["needs_manual_check"] = (
            f"{REQUEST_METRIC} 不可用或无数据，异常率未计算；"
            "需人工核实该指标的 path 标签是否覆盖 /api/voice/listen"
        )

    # 阈值判定：10 分钟窗口峰值 > 当前阈值 → 触发观察期关注
    report["threshold_exceeded"] = report["items"][f"peak_{interval_min}m_window"] > CURRENT_THRESHOLD
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceEntryUnassigned 观察期基线采集")
    parser.add_argument("--prometheus-url", default="http://localhost:9090", help="Prometheus 地址（默认 localhost:9090）")
    parser.add_argument("--window", default="7d", help="观察窗口（PromQL 时长格式，如 7d/14d，默认 7d）")
    parser.add_argument("--interval-min", type=int, default=10, help="告警对齐窗口分钟（默认 10，与规则一致）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 到 stdout")
    args = parser.parse_args()

    try:
        report = collect(args.prometheus_url, args.window, args.interval_min)
    except RuntimeError as exc:
        print(f"[ERROR] 采集失败: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        it = report["items"]
        print("=== VoiceEntryUnassigned 观察期基线 ===")
        print(f"采集时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report['collected_at']))}")
        print(f"Prometheus: {report['prometheus_url']} | 窗口: {report['window']} | 对齐窗口: {report['interval_min']}m")
        print(f"异常总量: {it['total_exceptions']:.0f}")
        print("按天分布（近 7 天，offset 1d=昨天）:")
        for d in it["daily_distribution"]:
            print(f"  {d['day_offset']}: {d['count']:.0f}")
        print(f"峰值(窗口内 max {report['interval_min']}m 增量): {it['peak_{m}m_window'.format(m=report['interval_min'])]:.0f}")
        if it["exception_rate"] is not None:
            print(f"异常率: {it['exception_rate']:.6f} (请求总量 {it['request_total']:.0f})")
        else:
            print(f"异常率: N/A ({report.get('needs_manual_check', '请求指标不可用')})")
        print(f"当前阈值 {CURRENT_THRESHOLD} 是否被峰值超过: {'是' if report['threshold_exceeded'] else '否'}")

    # 返回码：阈值被超过时返回 1（观察期关注项），供 CI 解析
    return 1 if report["threshold_exceeded"] else 0


if __name__ == "__main__":
    sys.exit(main())
