"""快速查询 Prometheus 规则和告警状态"""
import json
import urllib.request
import urllib.parse

PROM = "http://localhost:9090"

def query(q):
    url = f"{PROM}/api/v1/query?query={urllib.parse.quote(q)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())

def get_alerts():
    with urllib.request.urlopen(f"{PROM}/api/v1/alerts", timeout=10) as resp:
        return json.loads(resp.read().decode())

def get_rules():
    with urllib.request.urlopen(f"{PROM}/api/v1/rules", timeout=10) as resp:
        return json.loads(resp.read().decode())

# 列出所有规则组
print("="*60)
print("规则组列表")
print("="*60)
rules = get_rules()
groups = rules['data']['groups']
print(f"总组数: {len(groups)}")
health_rules = []
for g in groups:
    print(f"\n[{g['name']}] interval={g.get('interval')} rules={len(g['rules'])}")
    for r in g['rules']:
        name = r.get('name', '?')
        rtype = r.get('type', '?')
        health = r.get('health', '?')
        print(f"  - {name} (type={rtype}, health={health})")
        if name.startswith('yunshu:health:'):
            health_rules.append(name)

# 列出健康度录制规则名称
print("\n" + "="*60)
print("健康度录制规则列表（用于测试脚本验证）")
print("="*60)
for name in sorted(set(health_rules)):
    print(f"  {name}")

# 查询关键健康度评分当前值
print("\n" + "="*60)
print("当前健康度评分")
print("="*60)
queries = [
    'yunshu:health:overall_score',
    'yunshu:health:dimension:stability_score',
    'yunshu:health:dimension:performance_score',
    'yunshu:health:dimension:quality_score',
    'yunshu:health:dimension:efficiency_score',
    'yunshu:health:dimension:availability_score',
    'yunshu:health:dimension:security_score',
    'yunshu:health:stability:error_rate',
    'sum(yunshu_http_requests_total{status=~"5.."})',
    'sum(yunshu_http_requests_total)',
]
for q in queries:
    try:
        r = query(q)
        res = r.get('data', {}).get('result', [])
        if res:
            for item in res[:3]:
                v = item.get('value', [None, 'N/A'])[1]
                labels = item.get('metric', {})
                print(f"  {q} = {v}  {labels if labels else ''}")
        else:
            print(f"  {q} = <空>")
    except Exception as e:
        print(f"  {q} = ERROR: {e}")

# 当前告警
print("\n" + "="*60)
print("当前告警")
print("="*60)
alerts = get_alerts()
all_alerts = alerts['data']['alerts']
print(f"告警总数: {len(all_alerts)}")
for a in all_alerts[:30]:
    name = a['labels'].get('alertname', '?')
    state = a.get('state', '?')
    severity = a['labels'].get('severity', '?')
    print(f"  [{state}] {name} (severity={severity})")
