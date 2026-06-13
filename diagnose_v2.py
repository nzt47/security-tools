#!/usr/bin/env python3
"""
V2 功能诊断脚本
用于诊断和验证 DigitalLife V2 功能的完整状态
"""

import sys
import os
import logging
from pathlib import Path

# 设置环境变量
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from agent.digital_life import DigitalLife


def main():
    print("\n")
    print("=" * 100)
    print("[DIAGNOSE] DigitalLife V2 功能诊断")
    print("=" * 100)
    
    # 1. 测试完整 V2 配置
    print("\n[1/4] 测试完整 V2 配置...")
    config = {
        "features": {
            "v2_lifetrace": True,
            "v2_persona": True,
            "v2_distillation": True,
        },
        "lifetrace": {
            "data_dir": "./test_data/lifetrace_diag"
        },
        "distillation": {
            "data_dir": "./test_data/persona_diag",
            "interval": 5
        }
    }
    
    dl = DigitalLife(config=config)
    
    # 2. 检查 V2 功能状态
    print("\n[2/4] 检查 V2 功能状态...")
    features = dl.get_v2_features()
    print(f"   V2 功能状态: {features}")
    
    if features['v2_lifetrace'] and features['v2_persona'] and features['v2_distillation']:
        print("   [OK] 所有 V2 功能已正确启用")
    else:
        print("   [WARN] 部分 V2 功能未启用")
        print(f"      - v2_lifetrace: {features['v2_lifetrace']}")
        print(f"      - v2_persona: {features['v2_persona']}")
        print(f"      - v2_distillation: {features['v2_distillation']}")
    
    # 3. 获取性能报告
    print("\n[3/4] 获取性能报告...")
    perf_report = dl.get_performance_report()
    print(f"   性能报告: {perf_report}")
    
    # 4. 获取完整状态
    print("\n[4/4] 获取完整状态...")
    status = dl.get_status()
    print(f"   云枢版本: {status.get('云枢', {}).get('版本')}")
    print(f"   会话 ID: {status.get('云枢', {}).get('会话')}")
    
    if 'LifeTrace' in status:
        lt = status['LifeTrace']
        print(f"   LifeTrace:")
        print(f"      - 源节点数: {lt.get('源节点数', 0)}")
        print(f"      - 主题节点数: {lt.get('主题节点数', 0)}")
    
    if 'Persona' in status:
        p = status['Persona']
        print(f"   Persona:")
        print(f"      - 人格 ID: {p.get('人格ID')}")
        print(f"      - 版本: {p.get('版本')}")
    
    if '人格蒸馏' in status:
        d = status['人格蒸馏']
        print(f"   Distillation:")
        print(f"      - 启用: {d.get('启用')}")
        print(f"      - 学习间隔: {d.get('学习间隔')}")
    
    print("\n" + "=" * 100)
    print("[OK] 诊断完成！")
    print("=" * 100)
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n[ERROR] 诊断失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
