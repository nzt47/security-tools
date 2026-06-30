#!/usr/bin/env python3
"""内存泄漏模拟压测脚本

构造真实内存泄漏场景，端到端验证 resource_monitor 的告警与持久化能力：

验证点：
1. 高频采样（1 秒）能捕获内存增长曲线
2. 泄漏告警回调被触发（is_leaking=True）
3. 持久化文件生成且包含采样数据
4. 趋势分析（线性回归）斜率为正且超过阈值
5. 内存释放后曲线回落（无残留泄漏）

运行方式：
    python tests/stress/simulate_memory_leak.py
    python tests/stress/simulate_memory_leak.py --duration 30 --leak-rate 2
"""

import argparse
import gc
import json
import os
import sys
import time
from datetime import datetime

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.monitoring.resource_monitor import (
    ResourceMonitor,
    ResourceSnapshot,
    reset_resource_monitor,
)


def run_leak_simulation(duration_sec: int, leak_rate_mb: float, persist_path: str):
    """运行内存泄漏模拟

    Args:
        duration_sec: 运行时长（秒）
        leak_rate_mb: 每秒泄漏内存量（MB）
        persist_path: 持久化文件路径

    Returns:
        (leak_alerts, trend_result, persist_lines)
    """
    reset_resource_monitor()
    # 创建监控器：1 秒高频采样 + 低阈值便于触发告警 + 持久化
    monitor = ResourceMonitor(config={
        "sample_interval_sec": 1,
        "stress_test_interval_sec": 1,
        "leak_slope_threshold": 50000,  # 50KB/采样 即判定泄漏
        "history_size": 1000,
        "persist_enabled": True,
        "persist_path": persist_path,
        "persist_batch_size": 1,  # 每条立即落盘，便于验证
    })

    leak_alerts = []

    def on_leak(trend_result):
        leak_alerts.append({
            "timestamp": time.time(),
            "iso_time": datetime.now().isoformat(),
            "resource_type": trend_result.resource_type,
            "slope": round(trend_result.slope, 2),
            "threshold": trend_result.threshold,
            "r_squared": round(trend_result.r_squared, 4),
            "sample_count": trend_result.sample_count,
        })

    monitor.register_leak_callback(on_leak)

    print(f"=== 内存泄漏模拟压测 ===")
    print(f"时长: {duration_sec}s | 泄漏速率: {leak_rate_mb} MB/s | 阈值: 50000 bytes/采样")
    print(f"持久化路径: {persist_path}")
    print(f"开始时间: {datetime.now().isoformat()}")
    print()

    # 启动高频采样
    monitor.enable_stress_mode()
    monitor.start()

    # 模拟内存泄漏：每秒分配 leak_rate_mb MB 不释放
    leaked_blocks = []
    leak_bytes_per_sec = int(leak_rate_mb * 1024 * 1024)

    print("[阶段1] 注入内存泄漏...")
    for i in range(duration_sec):
        # 每秒分配一块内存（不释放）
        block = bytearray(leak_bytes_per_sec)
        # 填充非零数据避免被优化
        for j in range(0, len(block), 4096):
            block[j] = (i + j) % 256
        leaked_blocks.append(block)

        # 实时输出当前内存
        snap = monitor.get_snapshot()
        if snap:
            mem_mb = snap.memory.current_bytes / 1024 / 1024
            print(f"  [{i+1:3d}s] 内存={mem_mb:8.2f}MB | 累计泄漏={(i+1)*leak_rate_mb:6.1f}MB | 告警数={len(leak_alerts)}")

        time.sleep(1)

    # 阶段2：停止泄漏，观察释放曲线
    print()
    print("[阶段2] 停止泄漏，观察释放曲线...")
    monitor.stop()
    monitor.flush_persist()

    # 趋势分析
    trend = monitor.get_trend("memory")

    # 阶段3：释放内存，验证回归
    print()
    print("[阶段3] 释放泄漏内存...")
    del leaked_blocks
    gc.collect()
    time.sleep(1)
    # 触发一次采样记录释放后状态
    after_snap = monitor.sample()
    monitor.flush_persist()

    print()
    print("=== 验证结果 ===")

    # 验证1：持久化文件
    persist_lines = 0
    if os.path.exists(persist_path):
        with open(persist_path, "r", encoding="utf-8") as f:
            persist_lines = sum(1 for line in f if line.strip())
    persist_ok = persist_lines >= duration_sec
    print(f"[{'PASS' if persist_ok else 'FAIL'}] 持久化文件: {persist_lines} 条记录 (期望 >= {duration_sec})")

    # 验证2：泄漏告警触发
    alert_ok = len(leak_alerts) > 0
    print(f"[{'PASS' if alert_ok else 'FAIL'}] 泄漏告警: 触发 {len(leak_alerts)} 次")
    if leak_alerts:
        first = leak_alerts[0]
        print(f"       首次告警: slope={first['slope']} bytes/采样, r²={first['r_squared']}, 样本={first['sample_count']}")

    # 验证3：趋势分析
    trend_ok = trend is not None and trend.slope > 0 and trend.is_leaking
    if trend:
        print(f"[{'PASS' if trend_ok else 'FAIL'}] 趋势分析: slope={trend.slope:.2f}, r²={trend.r_squared:.4f}, is_leaking={trend.is_leaking}")
    else:
        print(f"[FAIL] 趋势分析: 无数据")

    # 验证4：内存释放后回落
    release_ok = False
    if after_snap:
        # 读取峰值内存（从历史中找最大值）
        history = monitor.get_history()
        if history:
            peak_mem = max(s.memory.current_bytes for s in history)
            after_mem = after_snap.memory.current_bytes
            release_ok = after_mem < peak_mem
            print(f"[{'PASS' if release_ok else 'FAIL'}] 释放验证: 峰值={peak_mem/1024/1024:.2f}MB → 释放后={after_mem/1024/1024:.2f}MB")
    else:
        print(f"[FAIL] 释放验证: 无快照")

    # 持久化状态
    persist_status = monitor.get_persist_status()
    print(f"[INFO] 持久化状态: 文件大小={persist_status['file_size_bytes']} bytes, 文件存在={persist_status['file_exists']}")

    print()
    print("=== 持久化文件采样（前 3 条）===")
    if os.path.exists(persist_path):
        with open(persist_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 3:
                    break
                data = json.loads(line)
                mem_mb = data.get("memory", {}).get("current_bytes", 0) / 1024 / 1024
                ts = data.get("iso_time", "")
                print(f"  [{ts}] 内存={mem_mb:.2f}MB")

    # 总结
    print()
    all_pass = persist_ok and alert_ok and trend_ok and release_ok
    print(f"=== 总结: {'全部通过' if all_pass else '存在失败项'} ===")

    monitor.stop()
    reset_resource_monitor()

    return leak_alerts, trend, persist_lines, all_pass


def main():
    parser = argparse.ArgumentParser(description="内存泄漏模拟压测")
    parser.add_argument("--duration", type=int, default=15, help="运行时长（秒，默认 15）")
    parser.add_argument("--leak-rate", type=float, default=2, help="每秒泄漏 MB（默认 2）")
    parser.add_argument("--persist-path", default="./data/leak_test_history.jsonl", help="持久化路径")
    args = parser.parse_args()

    # 确保持久化目录存在
    persist_dir = os.path.dirname(args.persist_path)
    if persist_dir:
        os.makedirs(persist_dir, exist_ok=True)
    # 清理旧文件
    if os.path.exists(args.persist_path):
        os.remove(args.persist_path)

    leak_alerts, trend, persist_lines, all_pass = run_leak_simulation(
        duration_sec=args.duration,
        leak_rate_mb=args.leak_rate,
        persist_path=args.persist_path,
    )

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
