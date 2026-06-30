"""快速查询当前告警恢复状态"""
import json
import urllib.request
import urllib.parse
from datetime import datetime

PROM = "http://localhost:9090"

def query(q):
    url = f"{PROM}/api/v1/query?query={urllib.parse.quote(q)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    res = data.get('data', {}).get('result', [])
    return float(res[0]['value'][1]) if res else None

def get_alerts():
    with urllib.request.urlopen(f"{PROM}/api/v1/alerts", timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data.get('data', {}).get('alerts', [])

now = datetime.now().isoformat()
print(f"当前时间: {now}")
print(f"距离错误注入停止约: 6-7 分钟\n")

# 查询关键指标
print("="*50)
print("当前指标")
print("="*50)
metrics = {
    'error_rate': 'yunshu:health:stability:error_rate',
    'stability_score': 'yunshu:health:dimension:stability_score',
    'overall_score': 'yunshu:health:overall_score',
}
for name, q in metrics.items():
    v = query(q)
    print(f"  {name}: {v}")

# 查询告警
print(f"\n{'='*50}")
print("当前告警")
print("="*50)
alerts = get_alerts()
print(f"总告警数: {len(alerts)}")
for a in alerts:
    name = a['labels'].get('alertname', '?')
    state = a.get('state', '?')
    severity = a['labels'].get('severity', '?')
    active = a.get('activeAt', '?')
    print(f"  [{state}] {name} (severity={severity}) activeAt={active}")

# 重点关注错误类告警
print(f"\n{'='*50}")
print("错误类告警恢复状态")
print("="*50)
error_alerts = [a for a in alerts if 'ErrorRate' in a['labels'].get('alertname', '') 
                or 'CriticalHTTP' in a['labels'].get('alertname', '')]
if not error_alerts:
    print("  ✓ 错误类告警已全部解除")
else:
    for a in error_alerts:
        name = a['labels'].get('alertname', '?')
        state = a.get('state', '?')
        print(f"  [{state}] {name} — 仍未解除")
