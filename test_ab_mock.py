#!/usr/bin/env python3
"""
A/B 实验框架 Mock 测试脚本

测试功能：
1. 创建实验并启动
2. 用户分流测试（确定性分配）
3. 指标收集测试
4. 实验结果分析

运行方式：
    python test_ab_mock.py
"""

import os
import sys
import json
import time
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def create_test_experiment(manager):
    """创建测试实验"""
    from agent.ab_testing import ExperimentVariant, ExperimentType

    print("\n" + "="*60)
    print("🎯 创建 A/B 实验")
    print("="*60)

    variants = [
        ExperimentVariant(
            variant_id="control",
            name="对照组 - 原有 Prompt",
            description="使用现有的标准提示词",
            weight=50,
            is_control=True,
            config={"prompt_version": "v1.0", "temperature": 0.7}
        ),
        ExperimentVariant(
            variant_id="test_a",
            name="实验组A - 优化版 Prompt",
            description="使用改进后的提示词，增强指令约束",
            weight=30,
            config={"prompt_version": "v2.0", "temperature": 0.5}
        ),
        ExperimentVariant(
            variant_id="test_b",
            name="实验组B - 精简版 Prompt",
            description="使用更精简的提示词，提高响应速度",
            weight=20,
            config={"prompt_version": "v2.1", "temperature": 0.6}
        ),
    ]

    exp = manager.create_experiment(
        name="Prompt 版本优化实验",
        experiment_type=ExperimentType.PROMPT_VERSION,
        variants=variants,
        description="测试不同 Prompt 版本对回答质量的影响",
        target_metric="quality_score",
        min_samples=50,
        significance_level=0.05
    )

    print(f"✅ 实验创建成功")
    print(f"   实验ID: {exp.experiment_id}")
    print(f"   实验名称: {exp.name}")
    print(f"   变体数量: {len(exp.variants)}")
    print(f"   目标指标: {exp.target_metric}")
    print(f"   最小样本量: {exp.min_samples}")

    for v in exp.variants:
        print(f"\n   ├─ 变体: {v.name} ({v.variant_id})")
        print(f"   │   权重: {v.weight}%")
        print(f"   │   类型: {'对照组' if v.is_control else '实验组'}")
        print(f"   │   配置: {json.dumps(v.config, ensure_ascii=False)}")

    return exp


def simulate_user_traffic(manager, experiment_id, user_count=100):
    """模拟用户流量"""
    print("\n" + "="*60)
    print("📊 模拟用户流量分流")
    print("="*60)

    variant_counts = {"control": 0, "test_a": 0, "test_b": 0}
    metrics = []

    for i in range(user_count):
        user_id = f"user_{i:04d}"
        
        # 模拟不同类型的用户
        user_type = random.choice(["new", "returning", "power"])
        
        # 分配变体
        variant = manager.assign_variant(experiment_id, user_id)
        
        if variant:
            variant_counts[variant.variant_id] += 1

            # 模拟指标收集
            base_score = {
                "control": 72,
                "test_a": 82,
                "test_b": 78
            }[variant.variant_id]
            
            quality_score = base_score + random.uniform(-10, 10)
            response_time = random.uniform(1.5, 4.0)
            cost = random.uniform(0.05, 0.2)
            
            success = manager.record_metric(
                experiment_id=experiment_id,
                variant_id=variant.variant_id,
                metric_type="quality_score",
                value=round(quality_score, 2),
                trace_id=f"trace_{user_id}",
                user_id=user_id,
                context={
                    "user_type": user_type,
                    "prompt_version": variant.config.get("prompt_version")
                }
            )

            manager.record_metric(
                experiment_id=experiment_id,
                variant_id=variant.variant_id,
                metric_type="response_time",
                value=round(response_time, 2),
                trace_id=f"trace_{user_id}",
                user_id=user_id
            )

            manager.record_metric(
                experiment_id=experiment_id,
                variant_id=variant.variant_id,
                metric_type="cost",
                value=round(cost, 4),
                trace_id=f"trace_{user_id}",
                user_id=user_id
            )

            if (i + 1) % 20 == 0:
                print(f"   已处理 {i+1}/{user_count} 用户...")

    print("\n📈 分流结果统计:")
    total = sum(variant_counts.values())
    for vid, count in variant_counts.items():
        percentage = (count / total) * 100 if total > 0 else 0
        print(f"   • {vid}: {count} 人 ({percentage:.1f}%)")

    # 验证确定性分配
    print("\n🔍 验证确定性分配（同一用户始终分到同一组）:")
    for user_id in ["user_0001", "user_0042", "user_0077"]:
        v1 = manager.assign_variant(experiment_id, user_id)
        v2 = manager.assign_variant(experiment_id, user_id)
        consistent = v1.variant_id == v2.variant_id if v1 and v2 else False
        status = "✅ 一致" if consistent else "❌ 不一致"
        print(f"   • {user_id}: {v1.variant_id if v1 else 'None'} {status}")

    return variant_counts


def analyze_experiment(manager, experiment_id):
    """分析实验结果"""
    print("\n" + "="*60)
    print("📋 实验结果分析")
    print("="*60)

    result = manager.analyze_results(experiment_id)

    print(f"\n📊 实验概览")
    print(f"   实验ID: {result.experiment_id}")
    print(f"   总样本量: {result.sample_size}")
    print(f"   是否显著: {'✅ 是' if result.is_significant else '❌ 否'}")
    print(f"   P值: {result.p_value}")
    print(f"   胜出组: {result.winner if result.winner else '无'}")

    print("\n📈 各变体统计:")
    for variant_id, stats in result.variant_results.items():
        print(f"\n   ┌─ {variant_id} ({stats['variant_name']})")
        print(f"   │   样本数: {stats['count']}")
        print(f"   │   平均分: {stats['mean']}")
        print(f"   │   标准差: {stats['std_dev']}")
        print(f"   │   最小值: {stats['min']}")
        print(f"   │   最大值: {stats['max']}")
        if 'z_score' in stats:
            print(f"   │   Z分数: {stats['z_score']}")
            print(f"   │   P值: {stats['p_value']}")
            sig_status = "✅ 显著" if stats['significant'] else "❌ 不显著"
            print(f"   │   显著性: {sig_status}")


def main():
    """主测试流程"""
    print("="*60)
    print("🚀 A/B 实验框架 Mock 测试")
    print("="*60)
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    from agent.ab_testing import get_ab_test_manager

    # 获取管理器
    print("\n🔧 初始化 A/B 实验管理器...")
    manager = get_ab_test_manager()
    print("✅ 管理器初始化完成")

    # 创建实验
    exp = create_test_experiment(manager)

    # 启动实验
    print("\n▶️ 启动实验...")
    manager.start_experiment(exp.experiment_id)
    exp = manager.get_experiment(exp.experiment_id)
    print(f"✅ 实验状态: {exp.status.value}")

    # 模拟用户流量
    simulate_user_traffic(manager, exp.experiment_id, user_count=100)

    # 分析结果
    analyze_experiment(manager, exp.experiment_id)

    print("\n" + "="*60)
    print("🎉 测试完成！")
    print("="*60)


if __name__ == "__main__":
    main()
