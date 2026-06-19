#!/usr/bin/env python3
"""
LifeTrace 压力测试脚本
测试并发边界、特殊字符处理、大数据量场景
"""

import time
import threading
import random
import string
import tempfile
import shutil
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lifetrace import TraceRecorder, MemoryRetriever
from lifetrace.enhanced_recorder import EnhancedTraceRecorder


class StressTestReport:
    """压力测试报告生成器"""
    
    def __init__(self):
        self.results = {
            "total_ops": 0,
            "success_ops": 0,
            "failed_ops": 0,
            "errors": [],
            "latencies": [],
            "test_cases": {}
        }
    
    def record_result(self, test_name, success, latency_ms=0, error=None):
        """记录测试结果"""
        if test_name not in self.results["test_cases"]:
            self.results["test_cases"][test_name] = {"success": 0, "failed": 0, "latencies": []}
        
        if success:
            self.results["success_ops"] += 1
            self.results["test_cases"][test_name]["success"] += 1
        else:
            self.results["failed_ops"] += 1
            self.results["test_cases"][test_name]["failed"] += 1
            if error:
                self.results["errors"].append({"test": test_name, "error": str(error)})
        
        self.results["total_ops"] += 1
        self.results["latencies"].append(latency_ms)
        self.results["test_cases"][test_name]["latencies"].append(latency_ms)
    
    def generate_report(self):
        """生成测试报告"""
        avg_latency = sum(self.results["latencies"]) / len(self.results["latencies"]) if self.results["latencies"] else 0
        max_latency = max(self.results["latencies"]) if self.results["latencies"] else 0
        min_latency = min(self.results["latencies"]) if self.results["latencies"] else 0
        success_rate = (self.results["success_ops"] / self.results["total_ops"]) * 100 if self.results["total_ops"] else 0
        
        report = []
        report.append("=" * 70)
        report.append("          LifeTrace 压力测试报告")
        report.append("=" * 70)
        report.append(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("-" * 70)
        report.append(f"总操作数: {self.results['total_ops']:,}")
        report.append(f"成功操作: {self.results['success_ops']:,}")
        report.append(f"失败操作: {self.results['failed_ops']:,}")
        report.append(f"成功率: {success_rate:.2f}%")
        report.append("-" * 70)
        report.append(f"平均延迟: {avg_latency:.2f} ms")
        report.append(f"最大延迟: {max_latency:.2f} ms")
        report.append(f"最小延迟: {min_latency:.2f} ms")
        report.append("-" * 70)
        
        # 各测试用例详情
        report.append("\n各测试用例详情:")
        for test_name, stats in self.results["test_cases"].items():
            tc_avg = sum(stats["latencies"]) / len(stats["latencies"]) if stats["latencies"] else 0
            tc_success = (stats["success"] / (stats["success"] + stats["failed"]) * 100) if (stats["success"] + stats["failed"]) > 0 else 0
            report.append(f"  {test_name}:")
            report.append(f"    成功: {stats['success']}, 失败: {stats['failed']}, 成功率: {tc_success:.2f}%, 平均延迟: {tc_avg:.2f}ms")
        
        # 错误详情
        if self.results["errors"]:
            report.append("\n错误详情:")
            for i, error in enumerate(self.results["errors"][:10], 1):  # 最多显示10个错误
                report.append(f"  {i}. [{error['test']}] {error['error']}")
            if len(self.results["errors"]) > 10:
                report.append(f"  ... 还有 {len(self.results['errors']) - 10} 个错误")
        
        report.append("\n" + "=" * 70)
        return "\n".join(report)


def generate_random_string(length):
    """生成随机字符串"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def generate_special_characters():
    """生成特殊字符组合"""
    special_cases = [
        "",  # 空字符串
        "".join(chr(i) for i in range(0, 32)),  # 控制字符
        "\n" * 100,  # 大量换行
        " " * 1000,  # 大量空格
        "".join([chr(0x4E00 + i) for i in range(100)]),  # 中文字符
        "🎭🎋🔥🎉👍",  # 表情符号
        "αβγδεζηθικλμνξοπρστυφχψω",  # 希腊字母
        "<script>alert('XSS')</script>",  # XSS攻击
        "' OR '1'='1",  # SQL注入
        "\\\"'`",  # 转义字符
        "a" * 10000,  # 超长字符串
    ]
    return random.choice(special_cases)


class LifeTraceStressTester:
    """LifeTrace 压力测试器"""
    
    def __init__(self, temp_dir):
        self.recorder = TraceRecorder(temp_dir)
        self.enhanced_recorder = EnhancedTraceRecorder(temp_dir)
        self.retriever = MemoryRetriever(
            self.recorder.source_tree,
            self.recorder.topic_tree,
            self.recorder.global_tree
        )
        self.report = StressTestReport()
        self.stop_event = threading.Event()
    
    def _measure_time(self, func, *args, **kwargs):
        """测量函数执行时间"""
        start = time.time()
        try:
            result = func(*args, **kwargs)
            latency = (time.time() - start) * 1000
            return True, latency, result, None
        except Exception as e:
            latency = (time.time() - start) * 1000
            return False, latency, None, e
    
    def test_chat_recording(self, iterations=1000):
        """测试对话记录性能"""
        print(f"[压力测试] 开始对话记录测试 ({iterations}次)")
        
        for i in range(iterations):
            content = generate_random_string(random.randint(10, 500))
            success, latency, _, error = self._measure_time(
                self.recorder.record_chat,
                role="user",
                content=content,
                auto_topic=False
            )
            self.report.record_result("chat_recording", success, latency, error)
            
            if (i + 1) % 200 == 0:
                print(f"  进度: {i + 1}/{iterations}")
    
    def test_special_characters(self, iterations=500):
        """测试特殊字符处理"""
        print(f"[压力测试] 开始特殊字符测试 ({iterations}次)")
        
        for i in range(iterations):
            content = generate_special_characters()
            success, latency, _, error = self._measure_time(
                self.recorder.record_chat,
                role="user",
                content=content,
                auto_topic=False
            )
            self.report.record_result("special_characters", success, latency, error)
    
    def test_concurrent_recording(self, threads=10, ops_per_thread=200):
        """测试并发记录"""
        print(f"[压力测试] 开始并发测试 ({threads}线程, 每线程{ops_per_thread}操作)")
        
        def worker(thread_id):
            for i in range(ops_per_thread):
                if self.stop_event.is_set():
                    break
                
                content = f"线程{thread_id}_消息{i}_{generate_random_string(50)}"
                success, latency, _, error = self._measure_time(
                    self.recorder.record_chat,
                    role="user",
                    content=content,
                    auto_topic=False
                )
                self.report.record_result("concurrent_recording", success, latency, error)
                
                # 混合传感器记录
                if i % 5 == 0:
                    success, latency, _, error = self._measure_time(
                        self.recorder.record_sensor,
                        sensor_type="test",
                        data={"thread": thread_id, "count": i, "value": random.random()}
                    )
                    self.report.record_result("concurrent_sensor", success, latency, error)
        
        threads_list = []
        for i in range(threads):
            t = threading.Thread(target=worker, args=(i,))
            threads_list.append(t)
            t.start()
        
        for t in threads_list:
            t.join()
    
    def test_retrieval_performance(self, iterations=500):
        """测试检索性能"""
        print(f"[压力测试] 开始检索性能测试 ({iterations}次)")
        
        # 先添加一些测试数据
        for i in range(100):
            self.recorder.record_chat("user", f"Python {i} 编程测试")
            self.recorder.record_chat("user", f"Java {i} 编程测试")
        
        search_queries = ["Python", "Java", "编程", "测试", "random", "数据"]
        
        for i in range(iterations):
            query = random.choice(search_queries)
            success, latency, _, error = self._measure_time(
                self.retriever.retrieve,
                query=query,
                limit=10
            )
            self.report.record_result("retrieval", success, latency, error)
    
    def test_large_data_volume(self, records=5000):
        """测试大数据量处理"""
        print(f"[压力测试] 开始大数据量测试 ({records}条记录)")
        
        for i in range(records):
            content = f"大规模数据测试记录 {i}: {generate_random_string(200)}"
            success, latency, _, error = self._measure_time(
                self.recorder.record_chat,
                role="user",
                content=content,
                auto_topic=False
            )
            self.report.record_result("large_data", success, latency, error)
            
            if (i + 1) % 1000 == 0:
                print(f"  进度: {i + 1}/{records}")
        
        # 测试统计获取
        success, latency, stats, error = self._measure_time(
            self.recorder.get_statistics
        )
        self.report.record_result("statistics", success, latency, error)
        if stats:
            print(f"  统计结果: 源节点={stats.get('source_nodes')}, 主题节点={stats.get('topic_nodes')}")
    
    def test_mixed_workload(self, iterations=1000):
        """测试混合工作负载"""
        print(f"[压力测试] 开始混合工作负载测试 ({iterations}次)")
        
        operations = [
            ("chat", lambda: self.recorder.record_chat("user", generate_random_string(100))),
            ("sensor", lambda: self.recorder.record_sensor("cpu", {"usage": random.uniform(0, 100)})),
            ("window", lambda: self.recorder.record_window(f"App_{random.randint(1, 10)}", "active")),
            ("file", lambda: self.recorder.record_file(f"/path/file_{random.randint(1, 100)}.txt", "modify")),
            ("topic", lambda: self.recorder.add_to_topic(f"主题{random.randint(1, 5)}", generate_random_string(50))),
            ("retrieve", lambda: self.retriever.retrieve(generate_random_string(10))),
        ]
        
        for i in range(iterations):
            op_name, op_func = random.choice(operations)
            success, latency, _, error = self._measure_time(op_func)
            self.report.record_result(f"mixed_{op_name}", success, latency, error)
    
    def run_all_tests(self):
        """运行所有压力测试"""
        print("\n" + "=" * 70)
        print("          LifeTrace 压力测试开始")
        print("=" * 70)
        
        # 运行各测试用例
        self.test_chat_recording(iterations=2000)
        self.test_special_characters(iterations=500)
        self.test_concurrent_recording(threads=10, ops_per_thread=300)
        self.test_retrieval_performance(iterations=500)
        self.test_large_data_volume(records=5000)
        self.test_mixed_workload(iterations=1000)
        
        # 生成报告
        print("\n" + self.report.generate_report())
        
        # 保存报告到文件
        report_path = f"lifetrace_stress_report_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(self.report.generate_report())
        print(f"\n报告已保存到: {report_path}")
        
        return self.report


if __name__ == "__main__":
    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="lifetrace_stress_")
    print(f"测试数据目录: {temp_dir}")
    
    try:
        tester = LifeTraceStressTester(temp_dir)
        report = tester.run_all_tests()
        
        # 检查测试是否通过
        if report.results["failed_ops"] == 0:
            print("\n✅ 所有压力测试通过！")
            sys.exit(0)
        else:
            print(f"\n❌ 压力测试有 {report.results['failed_ops']} 个失败")
            sys.exit(1)
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n已清理临时目录: {temp_dir}")