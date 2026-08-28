"""验证 Prometheus/Grafana 监控栈"""
import requests

# 1. Prometheus 规则状态
print("=== Prometheus 规则 ===")
try:
    r = requests.get("http://localhost:9090/api/v1/rules", timeout=5)
    data = r.json()
    groups = data.get("data", {}).get("groups", [])
    print(f"规则组: {len(groups)}")
    health_groups = [g for g in groups if "health" in g.get("name", "").lower()]
    print(f"健康度规则组: {len(health_groups)}")
    for g in health_groups:
        print(f"  - {g.get('name')}: {len(g.get('rules', []))} 条规则")
    # 列出所有组名
    print("全部规则组:")
    for g in groups:
        print(f"  - {g.get('name')}: {len(g.get('rules', []))} 条")
except Exception as e:
    print(f"❌ 获取规则失败: {e}")

# 2. Prometheus targets
print("\n=== Prometheus Targets ===")
try:
    r = requests.get("http://localhost:9090/api/v1/targets", timeout=5)
    data = r.json()
    targets = data.get("data", {}).get("activeTargets", [])
    for t in targets:
        print(f"  {t.get('labels', {}).get('job')} | {t.get('scrapeUrl')} | {t.get('health')}")
except Exception as e:
    print(f"❌ 获取 targets 失败: {e}")

# 3. 健康度指标查询
print("\n=== 健康度指标查询 ===")
try:
    r = requests.get("http://localhost:9090/api/v1/query",
                      params={"query": "yunshu_health_score"}, timeout=5)
    data = r.json()
    results = data.get("data", {}).get("result", [])
    if results:
        for item in results:
            print(f"  yunshu_health_score = {item.get('value')}")
    else:
        print("  ⚠️ 尚未抓取到 yunshu_health_score（可能指标未采集或命名不同）")
        # 尝试查询 yunshu:health
        r2 = requests.get("http://localhost:9090/api/v1/query",
                          params={"query": 'up{job="yunshu"}'}, timeout=5)
        d2 = r2.json()
        print(f"  up(job=yunshu) 结果: {d2.get('data', {}).get('result')}")
except Exception as e:
    print(f"❌ 查询失败: {e}")

# 4. Grafana 状态
print("\n=== Grafana ===")
try:
    r = requests.get("http://localhost:3000/api/health", timeout=5)
    print(f"  Grafana 健康: {r.json()}")
except Exception as e:
    print(f"  ❌ Grafana 不可达: {e}")
