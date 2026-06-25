"""查找实际存在的 HTTP 指标名"""
import json
import urllib.request
import urllib.parse

PROM = "http://localhost:9090"

# 查询所有 yunshu_ 开头的指标名
url = f"{PROM}/api/v1/label/__name__/values"
with urllib.request.urlopen(url, timeout=10) as resp:
    data = json.loads(resp.read().decode())

names = data.get('data', [])
print(f"总指标数: {len(names)}")

# 过滤 HTTP 相关
print("\n=== HTTP 相关指标 ===")
http_names = [n for n in names if 'http' in n.lower() or 'request' in n.lower()]
for n in sorted(http_names):
    print(f"  {n}")

# 过滤 yunshu_ 开头
print("\n=== yunshu_ 开头的所有指标 ===")
yunshu_names = [n for n in names if n.startswith('yunshu_')]
for n in sorted(yunshu_names):
    print(f"  {n}")

# 查询 yunshu_http_request_duration_seconds_count 的当前值
print("\n=== yunshu_http_request_duration_seconds_count 当前值 ===")
queries = [
    'yunshu_http_request_duration_seconds_count',
    'yunshu_http_request_duration_seconds_bucket{status="500"}',
    'yunshu_http_request_duration_seconds_bucket{status=~"5.."}',
    'sum(yunshu_http_request_duration_seconds_count) by (status)',
]
for q in queries:
    try:
        url = f"{PROM}/api/v1/query?query={urllib.parse.quote(q)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            r = json.loads(resp.read().decode())
        res = r.get('data', {}).get('result', [])
        print(f"\n[{q}] => {len(res)} 条")
        for item in res[:10]:
            v = item.get('value', [None, '?'])[1]
            labels = item.get('metric', {})
            print(f"  {v}  {labels}")
    except Exception as e:
        print(f"\n[{q}] ERROR: {e}")
