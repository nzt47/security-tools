"""健康度模拟异常数据注入脚本

用于测试告警规则，模拟各种异常场景：
1. 高错误率告警
2. 高延迟告警
3. 低健康度告警
4. 资源耗尽告警
5. 安全告警
"""
import requests
import json
import time
import random
from datetime import datetime

BASE_URL = "http://localhost:5678"


def inject_anomaly(anomaly_type: str, duration: int = 30):
    """注入异常数据
    
    Args:
        anomaly_type: 异常类型
        duration: 持续时间（秒）
    """
    scenarios = {
        "high_error_rate": {
            "name": "高错误率告警",
            "metrics": {
                "error_rate": 0.25,  # 25% 错误率
                "crash_count": 3,
                "retry_count": 50,
                "error_spike": True,
            },
            "expected_alert": "error_rate_above_threshold"
        },
        "high_latency": {
            "name": "高延迟告警",
            "metrics": {
                "p99_latency": 5.0,  # 5秒 P99延迟
                "p95_latency": 3.0,
                "latency_spike": True,
                "throughput": 2,
            },
            "expected_alert": "performance_latency_high"
        },
        "low_health_score": {
            "name": "低健康度告警",
            "metrics": {
                "error_rate": 0.15,
                "cpu_usage": 0.92,
                "memory_usage": 0.88,
                "p99_latency": 4.0,
                "schema_pass_rate": 0.75,
                "task_success_rate": 0.65,
                "security_alerts": 5,
            },
            "expected_alert": "health_score_critical"
        },
        "resource_exhaustion": {
            "name": "资源耗尽告警",
            "metrics": {
                "cpu_usage": 0.98,  # 98% CPU
                "memory_usage": 0.95,  # 95% 内存
                "disk_usage": 0.92,
            },
            "expected_alert": "resource_usage_critical"
        },
        "security_breach": {
            "name": "安全告警",
            "metrics": {
                "security_alerts": 10,
                "auth_fail_rate": 0.15,
                "anomaly_access": 25,
                "vulnerability_count": 3,
            },
            "expected_alert": "security_alerts_triggered"
        },
        "availability_issue": {
            "name": "可用性问题",
            "metrics": {
                "uptime": 0.94,
                "dependency_health": 0.60,
                "healthy_services": 2,
                "total_services": 5,
                "avg_recovery_time": 600,
            },
            "expected_alert": "availability_degraded"
        },
        "quality_degradation": {
            "name": "质量问题",
            "metrics": {
                "schema_pass_rate": 0.70,
                "critic_score": 55,
                "task_success_rate": 0.60,
                "tool_success_rate": 0.65,
            },
            "expected_alert": "quality_score_low"
        },
        "combined_critical": {
            "name": "复合严重告警",
            "metrics": {
                "error_rate": 0.35,
                "crash_count": 10,
                "cpu_usage": 0.95,
                "memory_usage": 0.92,
                "p99_latency": 8.0,
                "security_alerts": 15,
                "uptime": 0.88,
                "schema_pass_rate": 0.60,
            },
            "expected_alert": "multiple_critical"
        }
    }
    
    if anomaly_type not in scenarios:
        print(f"❌ 未知异常类型: {anomaly_type}")
        print(f"   可用类型: {', '.join(scenarios.keys())}")
        return
    
    scenario = scenarios[anomaly_type]
    print(f"\n{'='*60}")
    print(f"🚨 注入异常: {scenario['name']}")
    print(f"{'='*60}")
    print(f"📊 预期触发告警: {scenario['expected_alert']}")
    print(f"⏱️ 持续时间: {duration}秒")
    
    # 先获取当前状态
    print("\n📍 获取当前健康度...")
    try:
        resp = requests.get(f"{BASE_URL}/api/health/score", timeout=5)
        current = resp.json()
        print(f"   当前健康度: {current.get('overall_score', 'N/A')}")
        print(f"   当前等级: {current.get('level', 'N/A')}")
    except Exception as e:
        print(f"   获取失败: {e}")
    
    # 注入异常数据
    print(f"\n💉 注入异常指标...")
    print(f"   指标详情:")
    for key, value in scenario["metrics"].items():
        print(f"      - {key}: {value}")
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/health/score/calculate",
            json=scenario["metrics"],
            timeout=5
        )
        result = resp.json()
        if result.get("ok"):
            report = result["report"]
            print(f"\n✅ 异常注入成功!")
            print(f"   新健康度: {report.get('overall_score', 'N/A')}")
            print(f"   新等级: {report.get('level', 'N/A')}")
        else:
            print(f"\n❌ 注入失败: {result.get('error', 'Unknown')}")
    except Exception as e:
        print(f"\n❌ 请求失败: {e}")
    
    # 等待持续时间
    print(f"\n⏳ 等待 {duration} 秒...")
    for i in range(duration, 0, -5):
        time.sleep(min(5, i))
        remaining = min(5, i)
        # 模拟告警检查
        check_alerts(scenario["expected_alert"])
    
    print(f"\n🔄 恢复中...")
    # 注入正常数据恢复
    normal_metrics = {
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
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/health/score/calculate",
            json=normal_metrics,
            timeout=5
        )
        result = resp.json()
        if result.get("ok"):
            report = result["report"]
            print(f"✅ 已恢复! 当前健康度: {report.get('overall_score', 'N/A')}")
    except Exception as e:
        print(f"恢复请求失败: {e}")


def check_alerts(expected_alert: str):
    """检查告警状态"""
    try:
        resp = requests.get(f"{BASE_URL}/api/observability/alerts", timeout=5)
        data = resp.json()
        
        firing_count = 0
        for group in data.get("groups", []):
            for rule in group.get("rules", []):
                if rule.get("state") == "firing":
                    firing_count += 1
                    print(f"   🔔 触发告警: {rule.get('alert', 'Unknown')}")
        
        if firing_count == 0:
            print("   ✓ 暂无触发告警")
        else:
            print(f"   ⚠️ 共 {firing_count} 条告警触发")
            
    except Exception as e:
        print(f"   检查告警失败: {e}")


def run_preset_scenario():
    """运行预设场景序列"""
    scenarios = [
        ("high_error_rate", 20),
        ("high_latency", 15),
        ("resource_exhaustion", 20),
        ("security_breach", 25),
        ("combined_critical", 30),
    ]
    
    print("\n" + "="*60)
    print("🎬 开始运行预设场景序列")
    print("="*60)
    
    for i, (scenario, duration) in enumerate(scenarios, 1):
        print(f"\n[{i}/{len(scenarios)}] 场景 {i}")
        inject_anomaly(scenario, duration)
        time.sleep(5)  # 场景间隔
    
    print("\n" + "="*60)
    print("✅ 预设场景序列完成!")
    print("="*60)


def interactive_mode():
    """交互模式"""
    print("\n" + "="*60)
    print("🎮 健康度模拟异常注入 - 交互模式")
    print("="*60)
    
    scenarios = {
        "1": ("high_error_rate", "高错误率 (25% 错误率)"),
        "2": ("high_latency", "高延迟 (5秒 P99)"),
        "3": ("low_health_score", "低健康度 (<30分)"),
        "4": ("resource_exhaustion", "资源耗尽 (95%+ 资源)"),
        "5": ("security_breach", "安全告警 (10+ 告警)"),
        "6": ("availability_issue", "可用性问题 (94% 可用性)"),
        "7": ("quality_degradation", "质量问题 (Schema<70%)"),
        "8": ("combined_critical", "复合严重告警"),
        "a": ("all", "运行所有预设场景"),
        "q": ("quit", "退出"),
    }
    
    while True:
        print("\n📋 可用场景:")
        for key, (_, desc) in scenarios.items():
            print(f"   {key}. {desc}")
        
        choice = input("\n请选择场景 (1-8, a=全部, q=退出): ").strip()
        
        if choice == "q":
            print("再见!")
            break
        
        if choice not in scenarios:
            print("无效选择，请重试")
            continue
        
        scenario_type, _ = scenarios[choice]
        
        if scenario_type == "all":
            run_preset_scenario()
        else:
            duration = int(input("持续时间(秒，默认30): ").strip() or "30")
            inject_anomaly(scenario_type, duration)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # 命令行模式
        scenario = sys.argv[1]
        duration = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        
        if scenario == "all":
            run_preset_scenario()
        else:
            inject_anomaly(scenario, duration)
    else:
        # 交互模式
        interactive_mode()
