"""
灵犀感知底座示例入口

展示 BodySensor 的完整使用方式。
运行方式: python -m sensor.main
"""
from sensor import BodySensor, Category
import time
import os
import json
import sys

# 确保 UTF-8 输出（Windows 终端兼容）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def print_reading(reading):
    """格式化打印单个传感器读数"""
    sev_icon = {"normal": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}
    icon = sev_icon.get(reading.severity, "[?]")
    line = f"  {icon} {reading.description}: {reading.value}{reading.unit}"
    # Windows GBK 终端兼容处理
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("gbk", errors="replace").decode("gbk", errors="replace"))


def on_file_event(reading):
    """文件变动回调"""
    print(f"  [触觉感知] {reading.description}")


def main():
    print("=" * 60)
    print("  灵犀 (Lingxi) 感知底座 v2.0")
    print("  我是灵犀，让我感知一下身体状态...")
    print("=" * 60)

    # 创建 BodySensor，监听当前目录
    sensor = BodySensor(
        watch_dirs=[os.getcwd()],
        file_event_callback=on_file_event,
        enable_change_detection=True
    )

    # ─── 全量采集 ────────────────────────────────────
    print("\n[全量感知] 正在采集所有传感器数据...\n")
    all_data = sensor.collect_all()
    print(f"共采集到 {len(all_data)} 条感知信号\n")

    # 按类别分组显示
    categories = {}
    for r in all_data:
        cat = r.category or "other"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    for cat, readings in sorted(categories.items()):
        print(f"--- {cat.upper()} ({len(readings)} 条) ---")
        for r in readings[:3]:  # 每类只显示前 3 条
            print_reading(r)
        if len(readings) > 3:
            print(f"  ... 还有 {len(readings) - 3} 条")
        print()

    # ─── 健康报告 ────────────────────────────────────
    print("\n[健康报告]")
    print(sensor.get_health_report())

    # ─── 按类别采集 ──────────────────────────────────
    print("\n[按类别采集] 仅采集 CPU 数据:")
    cpu_readings = sensor.collect_category(Category.CPU)
    for r in cpu_readings[:5]:
        print_reading(r)

    # ─── JSON 输出示例 ───────────────────────────────
    print("\n[JSON 输出示例] (前 3 条)")
    for r in all_data[:3]:
        print(f"  {r.to_json()}")

    # ─── 建立变更基准 ───────────────────────────────
    print("\n[变更检测] 建立基准快照...")
    sensor.establish_baseline()

    # ─── 启动文件监听 ───────────────────────────────
    print(f"\n[文件监听] 开始监听当前目录变动...")
    print(f"  监听目录: {os.getcwd()}")
    print(f"  试试在另一个终端: echo test > test_file.txt")
    print(f"  按 Ctrl+C 退出\n")
    sensor.start_file_watch()

    try:
        while True:
            time.sleep(10)
            # 每 10 秒检查变更
            if sensor.change_detector:
                changes = sensor.change_detector.collect()
                if changes:
                    print(f"\n[变更感知] 检测到 {len(changes)} 项变化:")
                    for c in changes:
                        print_reading(c)
    except KeyboardInterrupt:
        print("\n[灵犀] 收到退出信号，正在休眠...")
        sensor.stop_file_watch()
        print("[灵犀] 再见，期待下次醒来。")


if __name__ == "__main__":
    main()
