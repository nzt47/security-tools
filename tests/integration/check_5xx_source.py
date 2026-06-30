"""查询当前 5xx 错误来源"""
import json
import urllib.request
import urllib.parse

PROM = "http://localhost:9090"

def query(q):
    url = f"{PROM}/api/v1/query?query={urllib.parse.quote(q)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data.get('data', {}).get('result', [])

# 1. 查询 5xx 错误按 endpoint 分布
print("="*60)
print("当前 5xx 错误按 endpoint 分布")
print("="*60)
queries = [
    ('sum by (endpoint, status) (yunshu_http_request_total{status=~"5.."})', '当前 5xx 总数'),
    ('sum by (endpoint, status) (rate(yunshu_http_request_total{status=~"5.."}[5m]))', '5xx 速率(5m)'),
    ('sum by (endpoint, status) (increase(yunshu_http_request_total{status=~"5.."}[10m]))', '5xx 增量(10m)'),
]
for q, desc in queries:
    print(f"\n[{desc}]")
    res = query(q)
    if not res:
        print("  <空>")
    for item in res:
        v = item.get('value', [None, '?'])[1]
        labels = item.get('metric', {})
        print(f"  {v}  endpoint={labels.get('endpoint', '?')}  status={labels.get('status', '?')}")

# 2. 查询所有请求按 status 分布
print(f"\n{'='*60}")
print("所有请求按 status 分布")
print("="*60)
res = query('sum by (status) (yunshu_http_request_total)')
for item in res:
    v = item.get('value', [None, '?'])[1]
    status = item.get('metric', {}).get('status', '?')
    print(f"  status={status}: {v}")

# 3. 当前告警
print(f"\n{'='*60}")
print("当前告警")
print("="*60)
with urllib.request.urlopen(f"{PROM}/api/v1/alerts", timeout=10) as resp:
    data = json.loads(resp.read().decode())
alerts = data.get('data', {}).get('alerts', [])
print(f"告警数: {len(alerts)}")
for a in alerts:
    name = a['labels'].get('alertname', '?')
    state = a.get('state', '?')
    print(f"  [{state}] {name}")
