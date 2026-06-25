"""检查 Prometheus targets 和抓取状态"""
import json
import urllib.request

PROM = "http://localhost:9090"

# 1. 检查所有 targets
print("="*60)
print("Prometheus Targets 状态")
print("="*60)
with urllib.request.urlopen(f"{PROM}/api/v1/targets", timeout=10) as resp:
    data = json.loads(resp.read().decode())

active = data['data']['activeTargets']
print(f"活跃 targets 数: {len(active)}")
for t in active:
    print(f"\n[{t['labels'].get('job', '?')}]")
    print(f"  health: {t['health']}")
    print(f"  scrapeUrl: {t['scrapeUrl']}")
    print(f"  lastError: {t.get('lastError', '')}")
    print(f"  lastScrape: {t.get('lastScrape', '')}")
    print(f"  lastScrapeDuration: {t.get('lastScrapeDurationSeconds', 0)}s")

# 2. 直接查询 yunshu_app 暴露的原始指标
print("\n" + "="*60)
print("查询 yunshu_* 指标")
print("="*60)
queries = [
    'yunshu_http_requests_total',
    'yunshu_cpu_usage_percent',
    'yunshu_memory_usage_percent',
    'yunshu_security_blocks_total',
    'yunshu_llm_calls_total',
    '{__name__=~"yunshu_.*"}',
]
for q in queries:
    try:
        url = f"{PROM}/api/v1/query?query={q}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            r = json.loads(resp.read().decode())
        res = r.get('data', {}).get('result', [])
        print(f"\n[{q}] => {len(res)} 条")
        for item in res[:5]:
            v = item.get('value', [None, '?'])[1]
            labels = item.get('metric', {})
            print(f"  {v}  {labels}")
    except Exception as e:
        print(f"\n[{q}] ERROR: {e}")

# 3. 直接访问应用 /metrics 端点
print("\n" + "="*60)
print("直接访问应用 /metrics 端点")
print("="*60)
import socket
# 测试 localhost:5678 是否可达
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    result = s.connect_ex(('localhost', 5678))
    s.close()
    print(f"localhost:5678 连接: {'开放' if result == 0 else '关闭 (err=%d)' % result}")
except Exception as e:
    print(f"localhost:5678 测试异常: {e}")

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    result = s.connect_ex(('host.docker.internal', 5678))
    s.close()
    print(f"host.docker.internal:5678 连接: {'开放' if result == 0 else '关闭 (err=%d)' % result}")
except Exception as e:
    print(f"host.docker.internal:5678 测试异常: {e}")

# 从主机侧直接访问 /metrics
try:
    import urllib.request
    with urllib.request.urlopen("http://localhost:5678/metrics", timeout=5) as resp:
        body = resp.read().decode()
    print(f"\nlocalhost:5678/metrics 返回 {len(body)} 字节")
    # 显示前 30 行
    for line in body.split('\n')[:30]:
        print(f"  {line}")
except Exception as e:
    print(f"\nlocalhost:5678/metrics 访问失败: {e}")
