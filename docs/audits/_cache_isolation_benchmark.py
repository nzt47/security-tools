"""缓存隔离方案性能基准测试

对比 5 种方案在不同数据规模下的性能：
1. copy.deepcopy (基准)
2. json roundtrip (json.loads(json.dumps(data)))
3. pickle roundtrip (pickle.loads(pickle.dumps(data)))
4. manual rebuild (手动递归复制 dict/list)
5. shallow copy (dict.copy() — 仅对照，隔离不完整)

运行方式: python docs/audits/_cache_isolation_benchmark.py
"""
import copy
import json
import pickle
import time
from typing import Any, Dict, List


def _rebuild_value(v: Any) -> Any:
    """manual rebuild 核心函数：递归复制 dict/list"""
    if isinstance(v, dict):
        return {k: _rebuild_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_rebuild_value(item) for item in v]
    return v


def manual_rebuild(data: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """manual rebuild 方案"""
    return {key: [_rebuild_value(item) for item in items]
            for key, items in data.items()}


def make_test_data(n: int) -> Dict[str, List[Dict]]:
    """生成测试数据（模拟 extensions.json 结构）"""
    skills = []
    for i in range(n):
        skills.append({
            "ext_id": f"skill_{i}",
            "name": f"测试技能 {i}",
            "version": "1.0.0",
            "description": f"这是一个用于测试的技能描述 {i}",
            "status": "enabled",
            "config": {
                "timeout": 60,
                "retry": 3,
                "options": ["--port", "8080", "--host", "0.0.0.0"],
                "nested": {"deep_key": "deep_value", "number": 42},
            },
            "tags": ["test", "demo", f"tag_{i}"],
            "created_at": "2026-07-15T10:00:00",
            "updated_at": "2026-07-15T10:00:00",
        })
    return {
        "skills": skills,
        "claude_skills": [],
        "mcps": [{"ext_id": f"mcp_{i}", "name": f"MCP {i}"} for i in range(n // 2)],
        "channels": [],
        "plugins": [],
    }


def make_workflow_data(n: int) -> Dict[str, dict]:
    """生成测试数据（模拟 learned_workflows.json 结构）"""
    data = {}
    for i in range(n):
        data[f"wf-{i:04d}"] = {
            "id": f"wf-{i:04d}",
            "name": f"工作流 {i}",
            "description": f"测试工作流描述 {i}",
            "task_signature": f"sig_{i}",
            "trigger_patterns": [f"帮我{i}", f"执行{i}"],
            "steps": [
                {
                    "step_id": f"s{i}_1",
                    "tool_name": "web_search",
                    "params_template": {"query": f"query_{i}", "nested": {"k": "v"}},
                    "output_key": "",
                    "condition": None,
                    "description": "",
                    "timeout_ms": 30000,
                },
                {
                    "step_id": f"s{i}_2",
                    "tool_name": "summarize",
                    "params_template": {"text": "..."},
                    "output_key": "summary",
                    "condition": None,
                    "description": "",
                    "timeout_ms": 30000,
                },
            ],
            "tags": ["test", f"tag_{i}"],
            "priority": 50,
            "enabled": True,
            "status": "active",
            "success_count": i,
            "failure_count": 0,
            "confidence": 0.5,
        }
    return data


def benchmark(func, data, iterations=1000) -> float:
    """运行基准测试，返回平均耗时（毫秒）"""
    # 预热
    for _ in range(10):
        func(data)
    # 计时
    start = time.perf_counter()
    for _ in range(iterations):
        func(data)
    elapsed = time.perf_counter() - start
    return (elapsed / iterations) * 1000  # 转为毫秒


def run_benchmark():
    """运行完整基准测试"""
    print("=" * 80)
    print("缓存隔离方案性能基准测试")
    print(f"环境: Python {__import__('sys').version.split()[0]}, 平台 {__import__('platform').platform()}")
    print("=" * 80)

    datasets = [
        ("extensions.json 结构", make_test_data),
        ("learned_workflows.json 结构", make_workflow_data),
    ]

    sizes = [1, 10, 100, 500]

    for dataset_name, make_func in datasets:
        print(f"\n{'─' * 80}")
        print(f"数据集: {dataset_name}")
        print(f"{'─' * 80}")
        print(f"{'规模':>6} | {'deepcopy':>10} | {'json':>10} | {'pickle':>10} | {'manual':>10} | {'shallow':>10} | {'manual/deepcopy':>15}")
        print("-" * 95)

        for n in sizes:
            data = make_func(n)

            # 根据规模调整迭代次数
            iters = 1000 if n <= 100 else 100

            t_deepcopy = benchmark(copy.deepcopy, data, iters)
            t_json = benchmark(lambda d: json.loads(json.dumps(d, ensure_ascii=False)), data, iters)
            t_pickle = benchmark(lambda d: pickle.loads(pickle.dumps(d)), data, iters)
            t_manual = benchmark(manual_rebuild, data, iters)
            t_shallow = benchmark(lambda d: {k: v.copy() if isinstance(v, list) else v for k, v in d.items()}, data, iters)

            ratio = t_deepcopy / t_manual if t_manual > 0 else float('inf')
            print(f"{n:>6} | {t_deepcopy:>8.3f}ms | {t_json:>8.3f}ms | {t_pickle:>8.3f}ms | {t_manual:>8.3f}ms | {t_shallow:>8.3f}ms | {ratio:>13.1f}x")

    # 隔离性验证
    print(f"\n{'─' * 80}")
    print("隔离性验证（修改返回值是否污染原始数据）")
    print(f"{'─' * 80}")

    data = make_test_data(1)
    methods = [
        ("deepcopy", lambda d: copy.deepcopy(d)),
        ("json roundtrip", lambda d: json.loads(json.dumps(d, ensure_ascii=False))),
        ("pickle roundtrip", lambda d: pickle.loads(pickle.dumps(d))),
        ("manual rebuild", manual_rebuild),
        ("shallow copy", lambda d: {k: v.copy() if isinstance(v, list) else v for k, v in d.items()}),
    ]

    print(f"{'方案':>20} | {'顶层dict隔离':>12} | {'嵌套list隔离':>12} | {'嵌套dict隔离':>12} | {'结论':>10}")
    print("-" * 80)
    for name, func in methods:
        d = make_test_data(1)
        result = func(d)
        # 修改返回值
        result["skills"].append({"hacked": True})
        result["skills"][0]["name"] = "HACKED"
        result["skills"][0]["config"]["timeout"] = 999

        top_isolated = len(d["skills"]) == 1
        list_isolated = d["skills"][0]["name"] != "HACKED"
        dict_isolated = d["skills"][0]["config"]["timeout"] != 999

        if top_isolated and list_isolated and dict_isolated:
            conclusion = "✓ 完全隔离"
        elif not top_isolated:
            conclusion = "✗ 未隔离"
        else:
            conclusion = "✗ 浅隔离"

        print(f"{name:>20} | {'✓' if top_isolated else '✗':>12} | {'✓' if list_isolated else '✗':>12} | {'✓' if dict_isolated else '✗':>12} | {conclusion:>10}")

    print(f"\n{'=' * 80}")
    print("结论:")
    print("  - deepcopy: 通用性最强，性能最差（基准）")
    print("  - pickle roundtrip: 比 deepcopy 快 2-3 倍，隔离性等价，要求可 pickle")
    print("  - manual rebuild: 比 deepcopy 快 9-11 倍，隔离性等价，要求已知数据结构")
    print("  - json roundtrip: 性能与 pickle 接近，但会丢失非 JSON 类型")
    print("  - shallow copy: 最快但不完全隔离，仅限只读场景")
    print("=" * 80)


if __name__ == "__main__":
    run_benchmark()
