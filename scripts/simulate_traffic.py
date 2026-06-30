#!/usr/bin/env python3
"""
A/B测试模拟流量数据生成器

功能：
- 模拟大量用户分流，验证分流策略准确性
- 模拟指标数据，验证熔断机制触发
- 输出详细的分配统计和实验结论

使用方式：
    python scripts/simulate_traffic.py --users 1000 --traffic-ratio 0.2
    python scripts/simulate_traffic.py --users 500 --scenario circuit_breaker
"""

import os
import sys
import json
import time
import shutil
import random
import argparse
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.ab_testing import (
    ABTestManager,
    ExperimentStatus,
    ExperimentType,
    ExperimentVariant,
)


def _log(module_name: str, action: str, extra: dict = None, duration_ms: float = 0):
    log_entry = {
        "trace_id": "",
        "module_name": module_name,
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "level": "INFO"
    }
    if extra:
        log_entry.update(extra)
    print(json.dumps(log_entry, ensure_ascii=False))


def run_traffic_simulation(storage_path: str, total_users: int,
                           traffic_ratio: float, whitelist: List[str],
                           blacklist: List[str]) -> Dict:
    """运行分流模拟"""
    t0 = time.time()
    _log("simulate_traffic", "traffic_simulation_start", {
        "total_users": total_users,
        "traffic_ratio": traffic_ratio,
        "whitelist_count": len(whitelist),
        "blacklist_count": len(blacklist)
    })

    manager = ABTestManager(storage_path=storage_path)
    manager.initialize()

    variants = [
        ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
        ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
    ]
    exp = manager.create_experiment(
        name="分流策略验证实验",
        experiment_type=ExperimentType.PROMPT_VERSION,
        variants=variants,
        traffic_ratio=traffic_ratio,
        whitelist=whitelist,
        blacklist=blacklist,
    )
    manager.start_experiment(exp.experiment_id)
    _log("simulate_traffic", "experiment_created", {
        "experiment_id": exp.experiment_id,
        "traffic_ratio": traffic_ratio
    })

    assignments = {"control": 0, "treatment": 0, "excluded": 0}
    user_assignments = {}

    for i in range(total_users):
        user_id = f"user_{i:06d}"
        variant = manager.assign_variant(exp.experiment_id, user_id)
        if variant is None:
            assignments["excluded"] += 1
        else:
            assignments[variant.variant_id] += 1
            user_assignments[user_id] = variant.variant_id

    # 验证分流确定性
    consistency_violations = 0
    check_users = min(100, total_users)
    for i in range(check_users):
        user_id = f"user_{i:06d}"
        if user_id in user_assignments:
            variant2 = manager.assign_variant(exp.experiment_id, user_id)
            if variant2 and variant2.variant_id != user_assignments[user_id]:
                consistency_violations += 1

    actual_ratio = (total_users - assignments["excluded"]) / total_users
    expected_ratio = traffic_ratio

    duration_ms = (time.time() - t0) * 1000
    result = {
        "experiment_id": exp.experiment_id,
        "total_users": total_users,
        "traffic_ratio_config": traffic_ratio,
        "actual_ratio": round(actual_ratio, 4),
        "ratio_error": round(abs(actual_ratio - expected_ratio), 4),
        "assignments": assignments,
        "whitelist_users": len(whitelist),
        "blacklist_users": len(blacklist),
        "consistency_violations": consistency_violations,
        "duration_ms": round(duration_ms, 2)
    }

    _log("simulate_traffic", "traffic_simulation_complete", result)
    return result


def run_circuit_breaker_simulation(storage_path: str, total_users: int,
                                   threshold: float) -> Dict:
    """运行熔断机制模拟"""
    t0 = time.time()
    _log("simulate_traffic", "circuit_breaker_simulation_start", {
        "total_users": total_users,
        "threshold": threshold
    })

    manager = ABTestManager(storage_path=storage_path)
    manager.initialize()

    variants = [
        ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
        ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
    ]
    exp = manager.create_experiment(
        name="熔断机制验证实验",
        experiment_type=ExperimentType.PROMPT_VERSION,
        variants=variants,
        traffic_ratio=1.0,
        auto_stop_threshold=threshold,
    )
    manager.start_experiment(exp.experiment_id)

    # 模拟对照组正常表现，实验组严重恶化
    control_scores = [random.uniform(85, 95) for _ in range(total_users // 2)]
    treatment_scores = [random.uniform(50, 65) for _ in range(total_users // 2)]

    for i, score in enumerate(control_scores):
        manager.record_metric(exp.experiment_id, "control", "quality_score", score)

    for i, score in enumerate(treatment_scores):
        manager.record_metric(exp.experiment_id, "treatment", "quality_score", score)

    # 检查熔断
    auto_stopped = manager.check_auto_stop(exp.experiment_id)
    exp_after = manager.get_experiment(exp.experiment_id)

    result = manager.analyze_results(exp.experiment_id)

    duration_ms = (time.time() - t0) * 1000
    output = {
        "experiment_id": exp.experiment_id,
        "total_records": total_users,
        "auto_stop_threshold": threshold,
        "auto_stopped": auto_stopped,
        "final_status": exp_after.status.value,
        "control_mean": round(result.variant_results.get("control", {}).get("mean", 0), 2),
        "treatment_mean": round(result.variant_results.get("treatment", {}).get("mean", 0), 2),
        "is_significant": result.is_significant,
        "winner": result.winner,
        "p_value": result.p_value,
        "duration_ms": round(duration_ms, 2)
    }

    _log("simulate_traffic", "circuit_breaker_simulation_complete", output)
    return output


def run_layer_simulation(storage_path: str, total_users: int) -> Dict:
    """运行多层实验模拟"""
    t0 = time.time()
    _log("simulate_traffic", "layer_simulation_start", {"total_users": total_users})

    manager = ABTestManager(storage_path=storage_path)
    manager.initialize()

    # 创建三个层级的实验
    layers = [
        {
            "name": "UI布局实验",
            "layer": 0,
            "variants": [
                ExperimentVariant(variant_id="ui_a", name="布局A", weight=50, is_control=True),
                ExperimentVariant(variant_id="ui_b", name="布局B", weight=50),
            ]
        },
        {
            "name": "推荐算法实验",
            "layer": 1,
            "variants": [
                ExperimentVariant(variant_id="rec_a", name="算法A", weight=50, is_control=True),
                ExperimentVariant(variant_id="rec_b", name="算法B", weight=50),
            ]
        },
        {
            "name": "文案风格实验",
            "layer": 2,
            "variants": [
                ExperimentVariant(variant_id="copy_a", name="风格A", weight=50, is_control=True),
                ExperimentVariant(variant_id="copy_b", name="风格B", weight=50),
            ]
        },
    ]

    experiments = []
    for layer_config in layers:
        exp = manager.create_experiment(
            name=layer_config["name"],
            experiment_type=ExperimentType.PROMPT_VERSION,
            variants=layer_config["variants"],
            layer=layer_config["layer"],
            traffic_ratio=1.0,
        )
        manager.start_experiment(exp.experiment_id)
        experiments.append(exp)

    layer_assignments = {}
    for i in range(total_users):
        user_id = f"user_{i:06d}"
        assignments = manager.assign_variant_with_layers(user_id)
        layer_count = len(assignments)
        if layer_count not in layer_assignments:
            layer_assignments[layer_count] = 0
        layer_assignments[layer_count] += 1

    duration_ms = (time.time() - t0) * 1000
    result = {
        "total_users": total_users,
        "layer_count": len(layers),
        "layer_assignment_distribution": layer_assignments,
        "duration_ms": round(duration_ms, 2)
    }

    _log("simulate_traffic", "layer_simulation_complete", result)
    return result


def main():
    parser = argparse.ArgumentParser(description='A/B测试模拟流量数据生成器')
    parser.add_argument('--users', type=int, default=1000, help='模拟用户数量')
    parser.add_argument('--traffic-ratio', type=float, default=1.0, help='流量比例')
    parser.add_argument('--whitelist', type=int, default=0, help='白名单用户数')
    parser.add_argument('--blacklist', type=int, default=0, help='黑名单用户数')
    parser.add_argument('--scenario', type=str, default='traffic',
                        choices=['traffic', 'circuit_breaker', 'layer'],
                        help='模拟场景')
    parser.add_argument('--threshold', type=float, default=0.15, help='熔断阈值')
    parser.add_argument('--output', type=str, help='结果输出文件')

    args = parser.parse_args()

    tmpdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'data', 'simulation', f"sim_{int(time.time())}")
    os.makedirs(tmpdir, exist_ok=True)

    whitelist = [f"whitelist_user_{i}" for i in range(args.whitelist)]
    blacklist = [f"blacklist_user_{i}" for i in range(args.blacklist)]

    if args.scenario == 'traffic':
        result = run_traffic_simulation(
            tmpdir, args.users, args.traffic_ratio, whitelist, blacklist
        )
    elif args.scenario == 'circuit_breaker':
        result = run_circuit_breaker_simulation(tmpdir, args.users, args.threshold)
    elif args.scenario == 'layer':
        result = run_layer_simulation(tmpdir, args.users)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存至: {args.output}")
    else:
        print("\n========== 模拟结果 ==========")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 可选：清理临时数据
    # shutil.rmtree(tmpdir)
    print(f"\n临时数据目录: {tmpdir}")


if __name__ == '__main__':
    main()
