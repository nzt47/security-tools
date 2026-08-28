"""检查告警规则和健康度指标状态"""
import requests
import json

BASE_URL = "http://localhost:5678"


def check_alerts():
    """检查告警规则"""
    print("=" * 60)
    print("🔔 告警规则检查")
    print("=" * 60)
    try:
        r = requests.get(f"{BASE_URL}/api/observability/alerts", timeout=5)
        data = r.json()
        groups = data.get("groups", [])
        print(f"告警组数量: {len(groups)}")
        if not groups:
            print("   ⚠️ 没有配置告警组!")
        for g in groups:
            print(f"  组: {g.get('name', '?')}")
            for rule in g.get("rules", []):
                state = rule.get("state", "?")
                severity = rule.get("labels", {}).get("severity", "?")
                marker = "🔴" if state == "firing" else "🟢"
                print(f"    {marker} {rule.get('alert', '?')} | 状态: {state} | 级别: {severity}")
                print(f"       expr: {rule.get('expr', '?')[:80]}")
    except Exception as e:
        print(f"❌ 检查告警失败: {e}")


def check_metrics():
    """检查 Prometheus 指标端点"""
    print("\n" + "=" * 60)
    print("📈 Prometheus /metrics 端点检查")
    print("=" * 60)
    try:
        r = requests.get(f"{BASE_URL}/metrics", timeout=5)
        if r.status_code == 200:
            lines = r.text.split("\n")
            health_metrics = [l for l in lines if "health" in l.lower() or "yunshu" in l.lower()]
            print(f"HTTP状态: {r.status_code}")
            print(f"总指标行数: {len(lines)}")
            print(f"健康度相关指标: {len(health_metrics)}")
            for m in health_metrics[:20]:
                print(f"   {m}")
        else:
            print(f"❌ /metrics 返回状态码: {r.status_code}")
    except Exception as e:
        print(f"❌ 检查 /metrics 失败: {e}")


def check_prometheus():
    """检查独立 Prometheus 服务是否运行"""
    print("\n" + "=" * 60)
    print("🔍 Prometheus 独立服务检查")
    print("=" * 60)
    for port in [9090, 9091]:
        try:
            r = requests.get(f"http://localhost:{port}/-/ready", timeout=2)
            print(f"   localhost:{port} → {'✅ 运行中' if r.status_code == 200 else f'状态码 {r.status_code}'}")
        except Exception:
            print(f"   localhost:{port} → ❌ 未运行")


def check_health_history():
    """检查健康度历史数据"""
    print("\n" + "=" * 60)
    print("📊 健康度历史数据")
    print("=" * 60)
    try:
        r = requests.get(f"{BASE_URL}/api/health/history?limit=10", timeout=5)
        data = r.json()
        history = data.get("history", [])
        print(f"历史记录数: {data.get('total', len(history))}")
        for h in history[-10:]:
            print(f"   {h.get('timestamp', '?')[:19]} | 得分: {h.get('overall_score')} | 等级: {h.get('level')}")
    except Exception as e:
        print(f"❌ 获取历史失败: {e}")


if __name__ == "__main__":
    check_alerts()
    check_metrics()
    check_prometheus()
    check_health_history()
