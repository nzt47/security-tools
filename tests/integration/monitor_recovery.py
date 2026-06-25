"""告警恢复监控脚本 - 10 分钟观察期

【生成日志摘要】
- 生成时间戳: 2026-06-25
- 内容描述: 错误注入停止后，监控告警自动恢复过程
- 版本: v1.0
- 关键状态变化: 告警 firing -> pending -> resolved

机制说明：使用 Request ID + 定时轮询，避免竞态；错误注入已停止，error_rate 会随 5min rate 窗口自然下降
"""
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

PROM = "http://localhost:9090"

def log(action: str, duration_ms: float = 0, **kwargs):
    entry = {
        "trace_id": "alert-recovery-monitor",
        "module_name": "recovery_monitor",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "timestamp": datetime.now().isoformat(),
        **kwargs
    }
    print(json.dumps(entry, ensure_ascii=False))

def query(q):
    start = time.time()
    try:
        url = f"{PROM}/api/v1/query?query={urllib.parse.quote(q)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        duration = (time.time() - start) * 1000
        res = data.get('data', {}).get('result', [])
        val = float(res[0]['value'][1]) if res else None
        log("query", duration, query=q, value=val)
        return val
    except Exception as e:
        duration = (time.time() - start) * 1000
        log("query_error", duration, query=q, error=str(e))
        # 边界显性化
        raise RuntimeError(f"RECOVERY_QUERY_001: {e}")

def get_alerts():
    start = time.time()
    try:
        with urllib.request.urlopen(f"{PROM}/api/v1/alerts", timeout=10) as resp:
            data = json.loads(resp.read().decode())
        duration = (time.time() - start) * 1000
        alerts = data.get('data', {}).get('alerts', [])
        log("get_alerts", duration, count=len(alerts))
        return alerts
    except Exception as e:
        duration = (time.time() - start) * 1000
        log("get_alerts_error", duration, error=str(e))
        raise RuntimeError(f"RECOVERY_ALERT_001: {e}")

def snapshot():
    """采集一次快照"""
    ts = datetime.now().isoformat()
    error_rate = query('yunshu:health:stability:error_rate')
    stability = query('yunshu:health:dimension:stability_score')
    overall = query('yunshu:health:overall_score')
    alerts = get_alerts()
    
    error_alerts = [a for a in alerts if 'ErrorRate' in a['labels'].get('alertname', '') 
                    or 'CriticalHTTP' in a['labels'].get('alertname', '')]
    
    print(f"\n[{ts}]")
    print(f"  error_rate = {error_rate}")
    print(f"  stability_score = {stability}")
    print(f"  overall_score = {overall}")
    print(f"  总告警数 = {len(alerts)}, 错误类告警 = {len(error_alerts)}")
    for a in error_alerts:
        name = a['labels'].get('alertname', '?')
        state = a.get('state', '?')
        print(f"    [{state}] {name}")
    
    return {
        'timestamp': ts,
        'error_rate': error_rate,
        'stability_score': stability,
        'overall_score': overall,
        'total_alerts': len(alerts),
        'error_alerts': len(error_alerts),
        'error_alert_details': [
            {'name': a['labels'].get('alertname'), 'state': a.get('state')}
            for a in error_alerts
        ]
    }

def main():
    print("="*60)
    print("  告警恢复监控（10 分钟观察期）")
    print("="*60)
    print(f"开始时间: {datetime.now().isoformat()}")
    print(f"错误注入已停止，观察 error_rate 自然下降")
    print(f"预期: error_rate < 0.05 后，YunshuHighErrorRate 解除")
    print(f"      1 分钟无新 5xx，YunshuCriticalHTTPErrors 解除")
    print()
    
    snapshots = []
    # 总观察 10 分钟，每 2 分钟采样一次（共 6 次）
    total_minutes = 10
    interval_minutes = 2
    samples = total_minutes // interval_minutes
    
    for i in range(samples):
        print(f"\n{'='*40}")
        print(f"  采样 {i+1}/{samples} (T+{i*interval_minutes}min)")
        print(f"{'='*40}")
        s = snapshot()
        snapshots.append(s)
        
        if i < samples - 1:
            print(f"\n  等待 {interval_minutes} 分钟...")
            time.sleep(interval_minutes * 60)
    
    # 最终判定
    print("\n" + "="*60)
    print("  恢复判定")
    print("="*60)
    
    final = snapshots[-1]
    initial = snapshots[0]
    
    print(f"\n[指标变化]")
    print(f"  error_rate: {initial['error_rate']} -> {final['error_rate']}")
    print(f"  stability_score: {initial['stability_score']} -> {final['stability_score']}")
    print(f"  overall_score: {initial['overall_score']} -> {final['overall_score']}")
    
    print(f"\n[告警变化]")
    print(f"  总告警数: {initial['total_alerts']} -> {final['total_alerts']}")
    print(f"  错误类告警: {initial['error_alerts']} -> {final['error_alerts']}")
    
    error_alerts_resolved = final['error_alerts'] == 0
    error_rate_dropped = (final['error_rate'] is not None and 
                          initial['error_rate'] is not None and
                          final['error_rate'] < initial['error_rate'])
    stability_recovered = (final['stability_score'] is not None and
                           initial['stability_score'] is not None and
                           final['stability_score'] > initial['stability_score'])
    
    print(f"\n[判定结果]")
    print(f"  {'✓' if error_rate_dropped else '✗'} error_rate 下降: {error_rate_dropped}")
    print(f"  {'✓' if stability_recovered else '✗'} stability_score 恢复: {stability_recovered}")
    print(f"  {'✓' if error_alerts_resolved else '✗'} 错误类告警解除: {error_alerts_resolved}")
    
    if error_alerts_resolved and error_rate_dropped:
        print("\n  [结论] 告警已自动恢复 ✓")
    elif error_rate_dropped and not error_alerts_resolved:
        print("\n  [结论] error_rate 已下降，告警待解除（for 持续时间未到）")
    else:
        print("\n  [结论] 告警尚未恢复，需继续观察")
    
    # 保存快照到文件
    with open('c:/Users/Administrator/agent/tests/integration/recovery_snapshots.json', 'w', encoding='utf-8') as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)
    print(f"\n快照已保存: tests/integration/recovery_snapshots.json")
    print(f"结束时间: {datetime.now().isoformat()}")

if __name__ == "__main__":
    main()
