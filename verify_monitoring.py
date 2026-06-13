#!/usr/bin/env python3
"""
简单性能监控验证脚本
用于验证 V2 模块的性能监控功能，不需要 Prometheus 依赖
"""

import sys
import os
import time
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from agent.digital_life import DigitalLife


def main():
    print("\n" + "=" * 70)
    print("🔍 性能监控验证")
    print("=" * 70)
    
    # 1. 创建 DigitalLife 实例
    print("\n[1/3] 初始化 DigitalLife...")
    dl = DigitalLife(config={
        "features": {
            "v2_lifetrace": True,
            "v2_persona": True,
            "v2_distillation": True,
        }
    })
    
    print("✅ 初始化完成")
    
    # 2. 获取初始性能报告
    print("\n[2/3] 获取初始性能报告...")
    perf_report = dl.get_performance_report()
    print("\n初始性能报告:")
    print(f"  模块统计: {list(perf_report['performance_summary'].keys())}")
    print(f"  启用模块: {list(perf_report['v2_modules'].items())}")
    
    for module, stats in perf_report['performance_summary'].items():
        print(f"\n  {module}:")
        print(f"    加载次数: {stats['count']}")
        print(f"    总耗时: {stats['total']:.2f} ms")
        print(f"    平均耗时: {stats['avg']:.2f} ms")
        print(f"    最小耗时: {stats['min']:.2f} ms")
        print(f"    最大耗时: {stats['max']:.2f} ms")
    
    # 3. 获取 V2 功能状态
    print("\n[3/3] 获取 V2 功能状态...")
    v2_status = dl.get_v2_features()
    print("\nV2 功能状态:")
    print(f"  v2_lifetrace: {v2_status['v2_lifetrace']}")
    print(f"  v2_persona: {v2_status['v2_persona']}")
    print(f"  v2_distillation: {v2_status['v2_distillation']}")
    print(f"  依赖模块可用:")
    for key, val in v2_status['available'].items():
        print(f"    {key}: {val}")
    
    print("\n" + "=" * 70)
    print("✅ 性能监控验证完成!")
    print("=" * 70)
    print("\n提示: 要启用 Prometheus 监控，请安装 prometheus_client 库:")
    print("  pip install prometheus_client")
    print("然后运行: python prometheus_example.py")
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ 验证失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
