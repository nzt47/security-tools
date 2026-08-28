"""健康度告警链路验证脚本"""
import requests
import time

BASE_URL = "http://localhost:5678"


def check_alert_rules():
    """检查已加载的健康度告警规则"""
    print("=" * 60)
    print("🔔 健康度告警规则")
    print("=" * 60)
    r = requests.get(f"{BASE_URL}/api/health/alerts", timeout=5)
    data = r.json()
    if not data.get("ok"):
        print(f"❌ {data}")
        return
    alerts = data.get("alerts", [])
    stats = data.get("stats", {})
    print(f"规则数: {len(alerts)}")
    for a in alerts:
        print(f"  - {a.get('name')}: {a.get('state')} | 阈值: {a.get('threshold')} | 级别: {a.get('severity')}")
    print(f"评估统计: {stats.get('evaluator', {})}")


def check_metrics():
    """检查 /metrics 健康度指标"""
    print("\n" + "=" * 60)
    print("📈 /metrics 健康度指标")
    print("=" * 60)
    r = requests.get(f"{BASE_URL}/metrics", timeout=5)
    lines = r.text.split("\n")
    health = [l for l in lines if "yunshu_health" in l]
    if health:
        for l in health:
            print(f"  {l}")
    else:
        print("  ⚠️ 无健康度指标，先触发一次评分")
        # 触发评分
        requests.get(f"{BASE_URL}/api/health/score", timeout=5)
        r = requests.get(f"{BASE_URL}/metrics", timeout=5)
        health = [l for l in r.text.split("\n") if "yunshu_health" in l]
        for l in health:
            print(f"  {l}")


def run_anomaly_test():
    """模拟高错误率并观察告警"""
    print("\n" + "=" * 60)
    print("🚨 高错误率模拟注入")
    print("=" * 60)

    # 1. 注入异常
    metrics = {
        "error_rate": 0.25,
        "crash_count": 3,
        "retry_count": 50,
        "error_spike": True,
    }
    r = requests.post(f"{BASE_URL}/api/health/score/calculate", json=metrics, timeout=5)
    result = r.json()
    if result.get("ok"):
        report = result["report"]
        print(f"✅ 注入成功! 健康度: {report['overall_score']} 等级: {report['level']}")
    else:
        print(f"❌ 注入失败: {result}")
        return

    # 2. 等待评估
    print(f"\n⏳ 等待 30 秒观察告警状态...")
    for i in range(6):
        time.sleep(5)
        r = requests.get(f"{BASE_URL}/api/health/alerts", timeout=5)
        data = r.json()
        firing = data.get("firing", [])
        pending = data.get("pending", [])
        all_alerts = data.get("alerts", [])
        active = [a for a in all_alerts if a.get("state") in ("firing", "pending")]
        if firing:
            print(f"   [{(i+1)*5}s] 🔴 触发: {[a['name'] for a in firing]}")
        elif active:
            print(f"   [{(i+1)*5}s] 🟡 待触发: {[a['name'] for a in active]}")
        else:
            print(f"   [{(i+1)*5}s] 🟢 无告警")
        # 检查 /metrics 健康度值变化
        m = requests.get(f"{BASE_URL}/metrics", timeout=5).text
        for line in m.split("\n"):
            if "yunshu_health_score " in line:
                print(f"       yunshu_health_score = {line.split()[-1]}")

    # 3. 恢复
    normal = {
        "error_rate": 0.01,
        "crash_count": 0,
        "cpu_usage": 0.40,
        "memory_usage": 0.50,
        "p99_latency": 1.0,
        "schema_pass_rate": 0.95,
        "task_success_rate": 0.90,
        "security_alerts": 0,
        "uptime": 0.999,
    }
    r = requests.post(f"{BASE_URL}/api/health/score/calculate", json=normal, timeout=5)
    result = r.json()
    if result.get("ok"):
        print(f"\n✅ 已恢复! 健康度: {result['report']['overall_score']}")

    # 4. 最终状态
    time.sleep(20)  # 等待恢复
    r = requests.get(f"{BASE_URL}/api/health/alerts", timeout=5)
    data = r.json()
    print(f"\n📋 恢复后告警状态:")
    for a in data.get("alerts", []):
        print(f"   {a.get('name')}: {a.get('state')}")


if __name__ == "__main__":
    check_alert_rules()
    check_metrics()
    run_anomaly_test()
