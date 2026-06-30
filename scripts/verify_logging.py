#!/usr/bin/env python3
"""验证 resource_monitor.py 新增日志的实际输出效果

运行方式: python scripts/verify_logging.py
"""
import logging
import os
import sys
import time

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 配置日志输出到 stdout，INFO 级别
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
# 同时开启 debug 级别，查看 trend_start/trend_skip/linear_regression 日志
logging.getLogger("agent.monitoring.resource_monitor").setLevel(logging.DEBUG)

from agent.monitoring.resource_monitor import ResourceMonitor

def main():
    print("=" * 80)
    print("资源监控日志验证脚本")
    print("=" * 80)

    # 创建监控器（禁用持久化，隔离测试）
    monitor = ResourceMonitor(config={"persist_enabled": False})

    print("\n--- 第 1 次采样（初始状态）---\n")
    monitor.sample()

    # 模拟少量内存增长
    leak = []
    print("\n--- 第 2 次采样（分配 1MB 内存后）---\n")
    leak.append(b"\x00" * (1024 * 1024))
    time.sleep(0.1)
    monitor.sample()

    print("\n--- 第 3 次采样（再分配 2MB 内存后）---\n")
    leak.append(b"\x00" * (2 * 1024 * 1024))
    time.sleep(0.1)
    monitor.sample()

    print("\n--- 趋势分析（memory）---\n")
    trend = monitor.get_trend("memory")
    if trend:
        print(f"\n趋势结果: slope={trend.slope:.2f}, r²={trend.r_squared:.4f}, "
              f"is_leaking={trend.is_leaking}, samples={trend.sample_count}")

    print("\n--- 趋势分析（thread，样本不足场景）---\n")
    # 只采样了 3 次，thread 趋势应该有结果但不会触发泄漏
    trend_thread = monitor.get_trend("thread")
    if trend_thread:
        print(f"\n线程趋势: slope={trend_thread.slope:.2f}, r²={trend_thread.r_squared:.4f}")

    print("\n--- 趋势分析（db_connection，无 provider 场景）---\n")
    trend_db = monitor.get_trend("db_connection")
    if trend_db:
        print(f"\nDB趋势: slope={trend_db.slope:.2f}")
    else:
        print("\nDB趋势: None（样本不足或无数据）")

    # 释放内存
    del leak

    print("\n" + "=" * 80)
    print("验证完成")
    print("=" * 80)

if __name__ == "__main__":
    main()
