"""
核心模块性能基准测试
Phase 3
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING)

# 直接导入而不是通过包
import importlib.util
spec = importlib.util.spec_from_file_location(
    "benchmark", 
    os.path.join(os.path.dirname(__file__), "__init__.py")
)
benchmark_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark_module)
BenchmarkSuite = benchmark_module.BenchmarkSuite


# 创建测试套件
core_suite = BenchmarkSuite("core_performance", output_dir="./data/benchmarks")


# 模拟一些简单的测试，实际项目可以集成真实模块

@core_suite.benchmark(name="simple_computation", iterations=1000)
def test_simple_computation():
    """简单计算测试"""
    result = 0
    for i in range(1000):
        result += i
    return result


@core_suite.benchmark(name="list_operations", iterations=500)
def test_list_operations():
    """列表操作测试"""
    test_list = []
    for i in range(1000):
        test_list.append(i)
    for i in range(500):
        test_list.pop()
    return test_list


@core_suite.benchmark(name="dictionary_operations", iterations=500)
def test_dict_operations():
    """字典操作测试"""
    test_dict = {}
    for i in range(1000):
        test_dict[f"key_{i}"] = f"value_{i}"
    for i in range(500):
        del test_dict[f"key_{i}"]
    return test_dict


@core_suite.benchmark(name="string_manipulation", iterations=1000)
def test_string_manipulation():
    """字符串操作测试"""
    result = ""
    for i in range(100):
        result += f"test_{i} "
    return result


# 记忆系统模拟测试
@core_suite.benchmark(name="memory_add", iterations=100)
def test_memory_add():
    """模拟记忆添加操作"""
    from datetime import datetime
    memory_store = []
    for i in range(100):
        memory_store.append({
            "id": f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            "content": f"Test content {i}",
            "timestamp": datetime.now().isoformat()
        })


@core_suite.benchmark(name="memory_search", iterations=100)
def test_memory_search():
    """模拟记忆搜索操作"""
    from datetime import datetime
    # 创建一些测试数据
    memory_store = []
    for i in range(1000):
        memory_store.append({
            "id": f"mem_{i}",
            "content": f"Test content {i} with keyword search",
            "timestamp": datetime.now().isoformat()
        })
    
    # 简单搜索
    query = "keyword"
    results = []
    for item in memory_store:
        if query in item["content"]:
            results.append(item)


# 运行所有测试并保存结果
if __name__ == "__main__":
    results = core_suite.run_all()
    core_suite.save_results()
    core_suite.generate_report()
    print(f"\n✓ {len(results)} benchmarks completed")
