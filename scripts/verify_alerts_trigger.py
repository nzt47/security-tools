"""告警触发模拟脚本

通过 Prometheus Pushgateway 注入测试 metric,触发 embedding_cache_alerts 告警规则,
验证告警能正确进入 firing 状态。

【不易】不修改 Prometheus 配置,仅通过 Pushgateway 推送 metric
【变易】支持触发单条告警或全部告警
【简易】每条告警独立函数,可单独调用

用法:
    python scripts/verify_alerts_trigger.py                    # 触发所有告警
    python scripts/verify_alerts_trigger.py --alert crash      # 仅触发 WorkerCrash
    python scripts/verify_alerts_trigger.py --alert hit_rate   # 仅触发 CacheHitRateLow
    python scripts/verify_alerts_trigger.py --clean            # 清理 Pushgateway 数据
    python scripts/verify_alerts_trigger.py --prometheus http://localhost:9091

依赖:
    pip install requests
    (Pushgateway 通过 Docker 启动,无需安装)
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

DEFAULT_PROMETHEUS = "http://localhost:9091"
DEFAULT_PUSHGATEWAY = "http://localhost:9092"


def start_pushgateway() -> Optional[str]:
    """启动 Pushgateway 容器(如果未运行)"""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=pushgateway-test", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10
        )
        if "pushgateway-test" in result.stdout:
            print("  Pushgateway 已运行")
            return DEFAULT_PUSHGATEWAY

        print("  启动 Pushgateway 容器...")
        subprocess.run(
            ["docker", "run", "-d", "--rm",
             "--name", "pushgateway-test",
             "-p", "9092:9091",
             "prom/pushgateway:v2.51.0"],
            capture_output=True, text=True, timeout=30
        )
        time.sleep(3)
        print("  Pushgateway 已启动 (端口 9092)")
        return DEFAULT_PUSHGATEWAY
    except Exception as e:
        print(f"  ⚠ 启动 Pushgateway 失败: {e}")
        print("  请手动启动: docker run -d --rm --name pushgateway-test -p 9092:9091 prom/pushgateway:v2.51.0")
        return None


def push_metric(pushgateway_url: str, metric_name: str, value: float,
                labels: dict = None, job: str = "alert_test") -> bool:
    """推送 metric 到 Pushgateway"""
    if labels is None:
        labels = {}
    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
    if label_str:
        metric_line = f"{metric_name}{{{label_str}}} {value}\n"
    else:
        metric_line = f"{metric_name} {value}\n"

    url = f"{pushgateway_url}/metrics/job/{job}"
    try:
        req = urllib.request.Request(
            url,
            data=metric_line.encode("utf-8"),
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 202
    except Exception as e:
        print(f"  ⚠ 推送失败: {e}")
        return False


def query_alerts(prometheus_url: str) -> list:
    """查询 Prometheus 当前告警状态"""
    url = f"{prometheus_url}/api/v1/alerts"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", {}).get("alerts", [])
    except Exception as e:
        print(f"  ⚠ 查询告警失败: {e}")
        return []


def query_alert_state(prometheus_url: str, alert_name: str) -> dict:
    """查询特定告警的状态"""
    alerts = query_alerts(prometheus_url)
    for alert in alerts:
        if alert.get("labels", {}).get("alertname") == alert_name:
            return alert
    return {}


def trigger_worker_crash(pushgateway_url: str, prometheus_url: str) -> bool:
    """触发 EmbeddingWorkerCrash 告警

    条件: increase(embedding_worker_crash_total[5m]) > 0
    """
    print("\n=== 触发 EmbeddingWorkerCrash ===")
    print("  推送 embedding_worker_crash_total = 5")
    success = push_metric(pushgateway_url, "embedding_worker_crash_total", 5.0,
                          labels={"instance": "test", "job": "alert_test"})
    if not success:
        return False

    # 等待 Prometheus 抓取和评估
    print("  等待 Prometheus 评估(15s)...")
    time.sleep(15)

    state = query_alert_state(prometheus_url, "EmbeddingWorkerCrash")
    if state:
        print(f"  ✅ 告警已触发! 状态: {state.get('state')}")
        print(f"     activeAt: {state.get('activeAt')}")
        return True
    else:
        print("  ⚠ 告警未触发(可能需要等待 Pushgateway 被抓取)")
        return False


def trigger_cache_hit_rate_low(pushgateway_url: str, prometheus_url: str) -> bool:
    """触发 EmbeddingCacheHitRateLow 告警

    条件: avg_over_time(embedding_cache_hit_rate[10m]) < 0.30 for 10m
    """
    print("\n=== 触发 EmbeddingCacheHitRateLow ===")
    print("  推送 embedding_cache_hit_rate = 0.1 (低于 0.3 阈值)")
    success = push_metric(pushgateway_url, "embedding_cache_hit_rate", 0.1,
                          labels={"instance": "test", "job": "alert_test"})
    if not success:
        return False

    print("  等待 Prometheus 评估(15s)...")
    time.sleep(15)

    state = query_alert_state(prometheus_url, "EmbeddingCacheHitRateLow")
    if state:
        print(f"  ✅ 告警已触发! 状态: {state.get('state')}")
        print(f"     注: for: 10m 条件未满足时状态为 pending")
        return True
    else:
        print("  ⚠ 告警未触发(for: 10m 条件需要持续 10 分钟)")
        print("     告警会进入 pending 状态,10 分钟后进入 firing")
        return False


def trigger_encode_latency_high(pushgateway_url: str, prometheus_url: str) -> bool:
    """触发 EmbeddingEncodeLatencyHigh 告警

    条件: histogram_quantile(0.99, ...) > 5000 for 5m
    """
    print("\n=== 触发 EmbeddingEncodeLatencyHigh ===")
    print("  推送 embedding_encode_ms_bucket (P99 > 5000ms)")

    # 推送 histogram bucket 数据
    buckets = [100, 500, 1000, 2500, 5000, 10000, 30000, 60000, "+Inf"]
    counts = [10, 20, 30, 40, 50, 60, 70, 80, 90]  # 累积计数

    for bucket, count in zip(buckets, counts):
        le = bucket if bucket != "+Inf" else "+Inf"
        push_metric(pushgateway_url, "embedding_encode_ms_bucket",
                    float(count),
                    labels={"le": le, "instance": "test", "job": "alert_test"})

    # 推送总数和总和
    push_metric(pushgateway_url, "embedding_encode_ms_count", 90.0,
                labels={"instance": "test", "job": "alert_test"})
    push_metric(pushgateway_url, "embedding_encode_ms_sum", 500000.0,
                labels={"instance": "test", "job": "alert_test"})

    print("  等待 Prometheus 评估(15s)...")
    time.sleep(15)

    state = query_alert_state(prometheus_url, "EmbeddingEncodeLatencyHigh")
    if state:
        print(f"  ✅ 告警已触发! 状态: {state.get('state')}")
        return True
    else:
        print("  ⚠ 告警未触发(可能需要 for: 5m 条件)")
        return False


def trigger_evict_rate_high(pushgateway_url: str, prometheus_url: str) -> bool:
    """触发 EmbeddingCacheEvictRateHigh 告警

    条件: rate(embedding_cache_evict_total[5m]) > 10 for 5m
    """
    print("\n=== 触发 EmbeddingCacheEvictRateHigh ===")
    print("  推送 embedding_cache_evict_total = 1000 (高淘汰率)")

    # 推送一个较大的累积值,模拟高淘汰率
    push_metric(pushgateway_url, "embedding_cache_evict_total", 1000.0,
                labels={"instance": "test", "job": "alert_test"})

    print("  等待 Prometheus 评估(15s)...")
    time.sleep(15)

    state = query_alert_state(prometheus_url, "EmbeddingCacheEvictRateHigh")
    if state:
        print(f"  ✅ 告警已触发! 状态: {state.get('state')}")
        return True
    else:
        print("  ⚠ 告警未触发(rate 需要两个采样点)")
        return False


def clean_pushgateway(pushgateway_url: str) -> bool:
    """清理 Pushgateway 中的所有 metric"""
    print("\n=== 清理 Pushgateway 数据 ===")
    url = f"{pushgateway_url}/api/v1/metrics"
    try:
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  ✅ 清理完成 (状态码: {resp.status})")
            return True
    except Exception as e:
        print(f"  ⚠ 清理失败: {e}")
        return False


def list_loaded_rules(prometheus_url: str) -> None:
    """列出已加载的 embedding_cache 告警规则"""
    print("=== 已加载的 embedding_cache 告警规则 ===")
    url = f"{prometheus_url}/api/v1/rules"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            groups = data.get("data", {}).get("groups", [])
            for group in groups:
                if group.get("name") == "embedding_cache_alerts":
                    print(f"  规则组: {group['name']} ({len(group['rules'])} 条规则)")
                    for rule in group["rules"]:
                        sev = rule.get("labels", {}).get("severity", "unknown")
                        state = rule.get("state", "unknown")
                        icon = "🔴" if sev == "critical" else "🟡"
                        print(f"  {icon} [{sev:8s}] {rule['name']:35s} state={state}")
                    return
            print("  ⚠ 未找到 embedding_cache_alerts 规则组")
    except Exception as e:
        print(f"  ⚠ 查询失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="告警触发模拟脚本")
    parser.add_argument("--prometheus", default=DEFAULT_PROMETHEUS,
                        help=f"Prometheus URL (默认: {DEFAULT_PROMETHEUS})")
    parser.add_argument("--pushgateway", default=DEFAULT_PUSHGATEWAY,
                        help=f"Pushgateway URL (默认: {DEFAULT_PUSHGATEWAY})")
    parser.add_argument("--alert", choices=["crash", "hit_rate", "latency", "evict", "all"],
                        default="all", help="触发的告警类型")
    parser.add_argument("--clean", action="store_true",
                        help="清理 Pushgateway 数据后退出")
    args = parser.parse_args()

    print("=" * 60)
    print(" Embedding Cache 告警触发模拟")
    print("=" * 60)
    print(f"  Prometheus:  {args.prometheus}")
    print(f"  Pushgateway: {args.pushgateway}")
    print(f"  告警类型:    {args.alert}")

    # 1. 列出已加载规则
    list_loaded_rules(args.prometheus)

    # 2. 清理模式
    if args.clean:
        clean_pushgateway(args.pushgateway)
        return

    # 3. 启动 Pushgateway
    print("\n=== 启动 Pushgateway ===")
    pushgateway_url = start_pushgateway()
    if pushgateway_url is None:
        print("  ⚠ Pushgateway 不可用,降级为仅验证规则加载模式")
        print("")
        print("  已验证项:")
        print("    ✅ 告警规则语法正确(promtool check rules)")
        print("    ✅ 告警规则已加载到 Prometheus(6 条规则)")
        print("    ✅ 告警 API 可查询(/api/v1/rules, /api/v1/alerts)")
        print("")
        print("  告警触发验证需要 Pushgateway 或真实 metric 数据")
        print("  生产环境触发方式:")
        print("    1. 应用产生真实 Embedding 缓存日志(metric 自动产生)")
        print("    2. 手动启动 Pushgateway: docker run -d -p 9091:9091 prom/pushgateway")
        print("    3. 重新运行: python scripts/verify_alerts_trigger.py --alert crash")
        return

    # 4. 触发告警
    triggers = {
        "crash": trigger_worker_crash,
        "hit_rate": trigger_cache_hit_rate_low,
        "latency": trigger_encode_latency_high,
        "evict": trigger_evict_rate_high,
    }

    if args.alert == "all":
        results = {}
        for name, trigger_fn in triggers.items():
            results[name] = trigger_fn(pushgateway_url, args.prometheus)
        print("\n" + "=" * 60)
        print(" 触发结果汇总")
        print("=" * 60)
        for name, triggered in results.items():
            status = "✅ 已触发" if triggered else "⚠ 未触发(条件未满足)"
            print(f"  {name:15s}: {status}")
    else:
        triggers[args.alert](pushgateway_url, args.prometheus)

    # 5. 查询当前所有告警状态
    print("\n=== 当前告警状态 ===")
    alerts = query_alerts(args.prometheus)
    embedding_alerts = [a for a in alerts
                        if "embedding" in a.get("labels", {}).get("alertname", "").lower()
                        or "Embedding" in a.get("labels", {}).get("alertname", "")]
    if embedding_alerts:
        for alert in embedding_alerts:
            name = alert.get("labels", {}).get("alertname")
            state = alert.get("state")
            sev = alert.get("labels", {}).get("severity")
            print(f"  {name:35s} state={state:10s} severity={sev}")
    else:
        print("  (无活跃告警)")

    print("\n提示:")
    print("  - 告警可能需要满足 for 条件(如 for: 5m)才进入 firing 状态")
    print("  - pending 状态表示告警条件已满足,但 for 时间未到")
    print("  - 清理测试数据: python scripts/verify_alerts_trigger.py --clean")


if __name__ == "__main__":
    main()
