"""健康度告警触发验证 - 结果写入文件"""
import requests
import time
import json

BASE_URL = "http://localhost:5678"
RESULT_FILE = "c:/Users/Administrator/agent/verify_alert_result.json"
log = []


def record(msg):
    log.append(msg)
    print(msg)


def main():
    # 1. 检查告警规则
    try:
        r = requests.get(f"{BASE_URL}/api/health/alerts", timeout=5)
        data = r.json()
        alerts = data.get("alerts", [])
        record(f"[规则] 加载健康度规则: {len(alerts)} 条")
        for a in alerts:
            record(f"  - {a['name']}: {a['state']} 阈值={a['threshold']}")
    except Exception as e:
        record(f"[规则] 获取失败: {e}")
        return

    # 2. 注入高错误率异常
    record("\n[注入] 模拟高错误率 (error_rate=25%)...")
    metrics = {"error_rate": 0.25, "crash_count": 3, "retry_count": 50, "error_spike": True}
    r = requests.post(f"{BASE_URL}/api/health/score/calculate", json=metrics, timeout=5)
    result = r.json()
    report = result.get("report", {})
    record(f"  健康度: {report.get('overall_score')} 等级: {report.get('level')}")

    # 3. 观察告警状态（45秒，每5秒查一次）
    record("\n[观察] 等待 45 秒...")
    for i in range(9):
        time.sleep(5)
        try:
            r = requests.get(f"{BASE_URL}/api/health/alerts", timeout=5)
            data = r.json()
            firing = [a["name"] for a in data.get("firing", [])]
            pending = [a["name"] for a in data.get("pending", [])]
            stats = data.get("stats", {}).get("evaluator", {})
            if firing:
                record(f"  [{(i+1)*5}s] 🔴 FIRING: {firing}")
            elif pending:
                record(f"  [{(i+1)*5}s] 🟡 PENDING: {pending}")
            else:
                record(f"  [{(i+1)*5}s] 🟢 无 (触发统计: {stats.get('alerts_triggered', 0)})")
        except Exception as e:
            record(f"  [{(i+1)*5}s] 查询失败: {e}")

    # 4. 恢复
    record("\n[恢复] 注入正常数据...")
    normal = {"error_rate": 0.01, "crash_count": 0, "cpu_usage": 0.40, "memory_usage": 0.50,
              "p99_latency": 1.0, "schema_pass_rate": 0.95, "task_success_rate": 0.90,
              "security_alerts": 0, "uptime": 0.999}
    r = requests.post(f"{BASE_URL}/api/health/score/calculate", json=normal, timeout=5)
    result = r.json()
    record(f"  健康度恢复: {result.get('report', {}).get('overall_score')}")

    # 5. 检查 /metrics
    r = requests.get(f"{BASE_URL}/metrics", timeout=5)
    for line in r.text.split("\n"):
        if "yunshu_health_score " in line or "yunshu_health_dimension_stability " in line:
            record(f"[metrics] {line}")

    # 写入结果文件
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    record(f"\n✅ 结果已写入 {RESULT_FILE}")


if __name__ == "__main__":
    main()
