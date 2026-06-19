"""懒加载版主启动入口

使用方法：
```bash
python main_lazy.py                    # 启动交互模式
python main_lazy.py --chat "你好"      # 单次对话
python main_lazy.py --status          # 查看状态
python main_lazy.py --perf            # 性能测试模式
```

特性：
- 启动时间 < 500ms
- 多级懒加载
- 详细的性能日志
"""

import argparse
import sys
import time
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="云枢 - 懒加载版本")
    parser.add_argument("--chat", type=str, help="单次对话模式")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--perf", action="store_true", help="性能测试模式")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    parser.add_argument("--count", type=int, default=10, help="性能测试次数")
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 性能测试模式
    if args.perf:
        run_performance_test(args.count)
        return
    
    # 导入并启动
    from agent.digital_life_lazy import LazyDigitalLife
    
    logger.info("=" * 70)
    logger.info("🚀 启动 LazyDigitalLife")
    logger.info("=" * 70)
    
    start_time = time.perf_counter()
    
    # 创建实例
    Yunshu = LazyDigitalLife()
    
    # 启动
    Yunshu.start()
    
    startup_time = (time.perf_counter() - start_time) * 1000
    
    logger.info("=" * 70)
    logger.info(f"✅ 启动完成！启动时间: {startup_time:.2f}ms")
    logger.info("=" * 70)
    
    # 查看状态
    if args.status:
        status = Yunshu.get_status()
        print("\n📊 状态报告:")
        print(f"  初始化: {status['initialized']}")
        print(f"  运行中: {status['started']}")
        print(f"  Important已加载: {status['important_loaded']}")
        print(f"  Important加载中: {status['important_loading']}")
        
        stats = status['load_stats']
        print(f"\n📈 加载统计:")
        print(f"  总尝试: {stats['total_attempts']}")
        print(f"  成功: {stats['successful_loads']}")
        print(f"  失败: {stats['failed_loads']}")
        print(f"  平均耗时: {stats['avg_load_time_ms']}")
        print(f"  已加载级别: {stats['loaded_levels']}")
        
        print(f"\n📦 模块状态:")
        for name, info in stats['modules'].items():
            status_icon = "✅" if info['loaded'] else "❌"
            error_info = f" ({info['error']})" if info['error'] else ""
            time_info = f" [{info['load_time_ms']}]" if info['load_time_ms'] else ""
            print(f"  {status_icon} {name}: {info['level']}{time_info}{error_info}")
        
        Yunshu.stop()
        return
    
    # 单次对话
    if args.chat:
        response = Yunshu.chat(args.chat)
        print(f"\n🤖 云枢: {response}")
        Yunshu.stop()
        return
    
    # 交互模式
    print("\n" + "=" * 70)
    print("🤖 云枢 (懒加载版本) - 输入你的问题，或输入 'quit' 退出")
    print("=" * 70)
    
    while True:
        try:
            user_input = input("\n👤 你: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ['quit', 'exit', '退出']:
                print("\n👋 再见！")
                break
            
            start = time.perf_counter()
            response = Yunshu.chat(user_input)
            elapsed = (time.perf_counter() - start) * 1000
            
            print(f"\n🤖 云枢 ({elapsed:.0f}ms): {response}")
            
        except KeyboardInterrupt:
            print("\n\n👋 再见！")
            break
        except Exception as e:
            print(f"\n❌ 错误: {e}")
    
    Yunshu.stop()


def run_performance_test(count: int = 10):
    """运行性能测试"""
    from agent.digital_life_lazy import LazyDigitalLife
    
    print("=" * 70)
    print(f"📊 启动时间性能测试 (n={count})")
    print("=" * 70)
    
    results = []
    
    for i in range(count):
        # 创建并启动
        start_time = time.perf_counter()
        Yunshu = LazyDigitalLife()
        Yunshu.start()
        elapsed = (time.perf_counter() - start_time) * 1000
        
        results.append(elapsed)
        print(f"第 {i+1:2d} 次: {elapsed:7.2f}ms")
        
        Yunshu.stop()
        
        # 短暂延迟
        time.sleep(0.1)
    
    # 统计
    print("-" * 70)
    
    results_sorted = sorted(results)
    avg = sum(results) / len(results)
    p50 = results_sorted[len(results) // 2]
    p95_idx = int(len(results_sorted) * 0.95)
    p95 = results_sorted[p95_idx]
    p99_idx = int(len(results_sorted) * 0.99)
    p99 = results_sorted[p99_idx]
    min_time = min(results)
    max_time = max(results)
    
    print(f"平均时间: {avg:7.2f}ms")
    print(f"P50时间:  {p50:7.2f}ms")
    print(f"P95时间:  {p95:7.2f}ms")
    print(f"P99时间:  {p99:7.2f}ms")
    print(f"最快时间: {min_time:7.2f}ms")
    print(f"最慢时间: {max_time:7.2f}ms")
    print("-" * 70)
    
    TARGET_MS = 500
    
    if p95 < TARGET_MS:
        print(f"✅ 测试通过！P95 启动时间 ({p95:.2f}ms) < 目标 ({TARGET_MS}ms)")
        print(f"   优化效果: {(max_time - p95) / max_time * 100:.1f}% 提升")
    else:
        print(f"❌ 测试未达标！P95 启动时间 ({p95:.2f}ms) >= 目标 ({TARGET_MS}ms)")
        print(f"   仍需优化: {(p95 - TARGET_MS):.2f}ms")


if __name__ == "__main__":
    main()
