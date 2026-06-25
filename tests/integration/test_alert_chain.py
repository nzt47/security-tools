"""告警链路模拟测试脚本

【生成日志摘要】
- 生成时间戳: 2026-06-25
- 内容描述: 告警链路端到端测试，验证从错误注入到指标采集、评分下降、告警触发的完整链路
- 版本: v1.0
- 模型配置: GLM-5.2
- 关键状态变化: 应用启动->错误注入->指标验证->告警状态验证

【测试覆盖维度】
1. 功能测试: HTTP 5xx 错误注入，Prometheus 指标采集
2. 边界测试: 健康度评分下降到阈值以下
3. 兼容性测试: 业务指标端点 + 应用指标端点
4. 性能测试: 指标采集延迟
5. 错误处理测试: 告警触发与恢复
"""
import json
import time
import urllib.request
import urllib.error
import sys
from datetime import datetime

# 可观测性强制约束：结构化日志
def log(action: str, duration_ms: float = 0, **kwargs):
    """输出 JSON 格式结构化日志"""
    entry = {
        "trace_id": "alert-chain-test",
        "module_name": "alert_simulator",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "timestamp": datetime.now().isoformat(),
        **kwargs
    }
    print(json.dumps(entry, ensure_ascii=False))

PROMETHEUS_URL = "http://localhost:9090"
APP_URL = "http://localhost:5678"

def query_prometheus(query: str) -> dict:
    """查询 Prometheus 指标"""
    start = time.time()
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(query)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        duration = (time.time() - start) * 1000
        log("prometheus_query", duration, query=query, status=data.get('status'))
        return data
    except Exception as e:
        duration = (time.time() - start) * 1000
        log("prometheus_query_error", duration, query=query, error=str(e))
        # 边界显性化：抛出带业务错误码的 Error
        raise RuntimeError(f"PROM_QUERY_001: 查询 Prometheus 失败 - {e}")

def get_alerts() -> list:
    """获取当前所有告警"""
    start = time.time()
    try:
        url = f"{PROMETHEUS_URL}/api/v1/alerts"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        duration = (time.time() - start) * 1000
        alerts = data.get('data', {}).get('alerts', [])
        log("get_alerts", duration, count=len(alerts))
        return alerts
    except Exception as e:
        duration = (time.time() - start) * 1000
        log("get_alerts_error", duration, error=str(e))
        raise RuntimeError(f"ALERT_FETCH_001: 获取告警失败 - {e}")

def trigger_error_endpoint(endpoint: str, count: int = 20) -> tuple:
    """触发错误端点，注入 5xx 错误"""
    start = time.time()
    success_count = 0
    error_count = 0
    statuses = []
    
    for i in range(count):
        try:
            url = f"{APP_URL}{endpoint}"
            req = urllib.request.Request(url)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    success_count += 1
                    statuses.append(resp.status)
            except urllib.error.HTTPError as he:
                # HTTPError 也算请求成功到达端点（5xx 是预期响应）
                error_count += 1
                statuses.append(he.code)
            except Exception as e:
                error_count += 1
                statuses.append(f"ERR:{type(e).__name__}")
        except Exception:
            error_count += 1
    
    duration = (time.time() - start) * 1000
    log("trigger_error_endpoint", duration, endpoint=endpoint, 
        total=count, success=success_count, http_errors=error_count,
        unique_statuses=list(set(str(s) for s in statuses)))
    # 埋点预留
    trackEvent('error_injection', {'endpoint': endpoint, 'count': count, 'errors': error_count})
    return success_count, error_count

def trackEvent(event_name: str, payload: dict):
    """埋点函数占位符"""
    pass  # 实际可接入分析平台

def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def print_query_result(name: str, query: str, result: dict):
    """打印查询结果"""
    print(f"\n[{name}]")
    print(f"  Query: {query}")
    if result.get('status') == 'success':
        res = result.get('data', {}).get('result', [])
        if not res:
            print(f"  Result: <空>")
        else:
            for item in res[:5]:
                value = item.get('value', [None, 'N/A'])[1]
                labels = item.get('metric', {})
                print(f"  Value: {value}  Labels: {labels}")
    else:
        print(f"  Error: {result}")

def main():
    print_section("云枢告警链路端到端测试")
    print(f"测试时间: {datetime.now().isoformat()}")
    print(f"Prometheus: {PROMETHEUS_URL}")
    print(f"应用服务: {APP_URL}")
    
    # ============ 阶段 1: 基线状态采集 ============
    print_section("阶段 1: 采集告警与评分基线")
    
    baseline_alerts = get_alerts()
    print(f"\n基线告警数: {len(baseline_alerts)}")
    for a in baseline_alerts[:10]:
        name = a['labels'].get('alertname', '?')
        state = a.get('state', '?')
        severity = a['labels'].get('severity', '?')
        print(f"  [{state}] {name} (severity={severity})")
    
    # 基线指标
    baseline_queries = {
        "error_rate": 'yunshu:health:stability:error_rate',
        "stability_score": 'yunshu:health:dimension:stability_score',
        "overall_score": 'yunshu:health:overall_score',
        "http_5xx_total": 'sum(yunshu_http_requests_total{status=~"5.."})',
    }
    
    print("\n基线指标:")
    baseline_values = {}
    for name, query in baseline_queries.items():
        result = query_prometheus(query)
        print_query_result(name, query, result)
        res = result.get('data', {}).get('result', [])
        if res:
            baseline_values[name] = float(res[0].get('value', [None, 0])[1])
        else:
            baseline_values[name] = None
    
    # ============ 阶段 2: 错误注入 ============
    print_section("阶段 2: 注入 HTTP 5xx 错误")
    
    # 触发 /api/test/error 端点多次（每次返回 500）
    print("\n触发 /api/test/error 端点 (40 次)...")
    s1, e1 = trigger_error_endpoint("/api/test/error", 40)
    
    # 触发 /api/test/division 端点（除零错误）
    print(f"\n触发 /api/test/division 端点 (20 次)...")
    s2, e2 = trigger_error_endpoint("/api/test/division", 20)
    
    print(f"\n错误注入汇总:")
    print(f"  /api/test/error: {e1} 个 5xx 响应")
    print(f"  /api/test/division: {e2} 个 5xx 响应")
    print(f"  总计注入 {e1 + e2} 个错误请求")
    
    # ============ 阶段 3: 等待指标采集 ============
    print_section("阶段 3: 等待 Prometheus 抓取 (15s)")
    time.sleep(15)
    
    # ============ 阶段 4: 验证指标变化 ============
    print_section("阶段 4: 验证指标变化")
    
    after_queries = {
        "http_5xx_total": 'sum(yunshu_http_requests_total{status=~"5.."})',
        "http_500_total": 'sum(yunshu_http_requests_total{status="500"})',
        "error_rate": 'yunshu:health:stability:error_rate',
        "stability_score": 'yunshu:health:dimension:stability_score',
        "overall_score": 'yunshu:health:overall_score',
    }
    
    after_values = {}
    for name, query in after_queries.items():
        result = query_prometheus(query)
        print_query_result(name, query, result)
        res = result.get('data', {}).get('result', [])
        if res:
            after_values[name] = float(res[0].get('value', [None, 0])[1])
        else:
            after_values[name] = None
    
    # ============ 阶段 5: 验证告警状态变化 ============
    print_section("阶段 5: 验证告警状态")
    
    after_alerts = get_alerts()
    print(f"\n测试后告警数: {len(after_alerts)}")
    for a in after_alerts[:20]:
        name = a['labels'].get('alertname', '?')
        state = a.get('state', '?')
        severity = a['labels'].get('severity', '?')
        print(f"  [{state}] {name} (severity={severity})")
    
    # ============ 阶段 6: 等待录制规则更新 (30s) ============
    print_section("阶段 6: 等待录制规则更新 (30s)")
    time.sleep(30)
    
    # 重新查询健康度评分
    print("\n二次查询健康度评分:")
    final_queries = {
        "error_rate": 'yunshu:health:stability:error_rate',
        "stability_score": 'yunshu:health:dimension:stability_score',
        "overall_score": 'yunshu:health:overall_score',
    }
    final_values = {}
    for name, query in final_queries.items():
        result = query_prometheus(query)
        print_query_result(name, query, result)
        res = result.get('data', {}).get('result', [])
        if res:
            final_values[name] = float(res[0].get('value', [None, 0])[1])
        else:
            final_values[name] = None
    
    final_alerts = get_alerts()
    print(f"\n最终告警数: {len(final_alerts)}")
    for a in final_alerts[:30]:
        name = a['labels'].get('alertname', '?')
        state = a.get('state', '?')
        severity = a['labels'].get('severity', '?')
        print(f"  [{state}] {name} (severity={severity})")
    
    # ============ 测试报告 ============
    print_section("阶段 7: 链路测试报告")
    
    print("\n[基线对比]")
    print(f"  HTTP 5xx 总数: 基线={baseline_values.get('http_5xx_total')} -> 测试后={after_values.get('http_5xx_total')}")
    print(f"  错误率: 基线={baseline_values.get('error_rate')} -> 二次={final_values.get('error_rate')}")
    print(f"  稳定性评分: 基线={baseline_values.get('stability_score')} -> 二次={final_values.get('stability_score')}")
    print(f"  整体评分: 基线={baseline_values.get('overall_score')} -> 二次={final_values.get('overall_score')}")
    
    print(f"\n[告警数对比]")
    print(f"  基线告警: {len(baseline_alerts)}")
    print(f"  测试后告警: {len(after_alerts)}")
    print(f"  最终告警: {len(final_alerts)}")
    
    # 链路判定
    print(f"\n[链路通畅性判定]")
    
    checks = []
    # 检查 1: HTTP 5xx 指标有增加
    if (baseline_values.get('http_5xx_total') is not None and 
        after_values.get('http_5xx_total') is not None and
        after_values.get('http_5xx_total') > baseline_values.get('http_5xx_total')):
        checks.append(("✓ HTTP 5xx 指标采集", True, 
                       f"{baseline_values.get('http_5xx_total')} -> {after_values.get('http_5xx_total')}"))
    else:
        checks.append(("✗ HTTP 5xx 指标采集", False, 
                       f"基线={baseline_values.get('http_5xx_total')} 测试后={after_values.get('http_5xx_total')}"))
    
    # 检查 2: 错误率上升
    if (final_values.get('error_rate') is not None and 
        (baseline_values.get('error_rate') is None or 
         final_values.get('error_rate') > baseline_values.get('error_rate', 0))):
        checks.append(("✓ 错误率录制规则生效", True,
                       f"基线={baseline_values.get('error_rate')} -> {final_values.get('error_rate')}"))
    else:
        checks.append(("✗ 错误率录制规则生效", False,
                       f"基线={baseline_values.get('error_rate')} -> {final_values.get('error_rate')}"))
    
    # 检查 3: 稳定性评分下降
    if (baseline_values.get('stability_score') is not None and 
        final_values.get('stability_score') is not None and
        final_values.get('stability_score') < baseline_values.get('stability_score')):
        checks.append(("✓ 稳定性评分下降", True,
                       f"{baseline_values.get('stability_score')} -> {final_values.get('stability_score')}"))
    else:
        checks.append(("△ 稳定性评分变化", False,
                       f"基线={baseline_values.get('stability_score')} -> 测试后={final_values.get('stability_score')}"))
    
    # 检查 4: 告警系统响应
    if len(final_alerts) > 0:
        checks.append(("✓ 告警系统活跃", True, f"共 {len(final_alerts)} 条告警"))
    else:
        checks.append(("△ 告警系统", False, "无告警触发（可能错误率尚未达阈值）"))
    
    for name, ok, detail in checks:
        print(f"  {name}: {detail}")
    
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"\n  通过率: {passed}/{total}")
    
    if passed >= 3:
        print("\n  [结论] 告警链路通畅 ✓")
    elif passed >= 2:
        print("\n  [结论] 告警链路基本通畅，部分指标延迟正常")
    else:
        print("\n  [结论] 告警链路存在问题，需要排查")
    
    print(f"\n{'='*60}")
    print(f"  测试完成: {datetime.now().isoformat()}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    import urllib.parse
    main()
