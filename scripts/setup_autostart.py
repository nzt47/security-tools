#!/usr/bin/env python3
"""
Windows 自动启动配置脚本

将 Prometheus 监控集成到 Windows 任务计划程序，确保系统重启后监控自动运行。

使用方式：
    python setup_autostart.py --install    # 安装自动启动任务
    python setup_autostart.py --uninstall  # 卸载自动启动任务
    python setup_autostart.py --status     # 查看任务状态
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 任务计划程序任务名称
TASK_NAME = "YunshuV2PrometheusMonitor"

def get_python_path():
    """获取 Python 可执行文件路径"""
    return sys.executable

def get_script_path():
    """获取 prometheus_example.py 路径"""
    return str(project_root / "prometheus_example.py")

def get_log_path():
    """获取日志文件路径"""
    return str(project_root / "prometheus_autostart.log")

def install_task():
    """安装 Windows 任务计划程序任务"""
    python_path = get_python_path()
    script_path = get_script_path()
    log_path = get_log_path()
    
    print(f"\n[INFO] Installing Windows Task Scheduler task...")
    print(f"[INFO] Task name: {TASK_NAME}")
    print(f"[INFO] Python: {python_path}")
    print(f"[INFO] Script: {script_path}")
    print(f"[INFO] Log: {log_path}")
    
    # 创建任务计划程序任务
    # 任务将在系统启动时自动运行
    command = [
        "schtasks",
        "/create",
        "/tn", TASK_NAME,
        "/tr", f'"{python_path}" "{script_path}" --quiet',
        "/sc", "onstart",  # 系统启动时运行
        "/ru", "SYSTEM",   # 以系统用户运行
        "/rl", "HIGHEST",  # 最高权限
        "/f"               # 强制创建（覆盖已存在的任务）
    ]
    
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"\n[OK] Task '{TASK_NAME}' installed successfully!")
            print(f"[INFO] The monitoring will start automatically when system boots.")
            print(f"\n[INFO] To manually start the task:")
            print(f"  schtasks /run /tn {TASK_NAME}")
            print(f"\n[INFO] To check task status:")
            print(f"  schtasks /query /tn {TASK_NAME}")
            return True
        else:
            print(f"\n[ERROR] Failed to install task:")
            print(f"  {result.stderr}")
            return False
            
    except Exception as e:
        print(f"\n[ERROR] Exception: {e}")
        return False

def uninstall_task():
    """卸载 Windows 任务计划程序任务"""
    print(f"\n[INFO] Uninstalling Windows Task Scheduler task...")
    print(f"[INFO] Task name: {TASK_NAME}")
    
    command = [
        "schtasks",
        "/delete",
        "/tn", TASK_NAME,
        "/f"  # 强制删除
    ]
    
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"\n[OK] Task '{TASK_NAME}' uninstalled successfully!")
            return True
        else:
            print(f"\n[ERROR] Failed to uninstall task:")
            print(f"  {result.stderr}")
            return False
            
    except Exception as e:
        print(f"\n[ERROR] Exception: {e}")
        return False

def query_task():
    """查询 Windows 任务计划程序任务状态"""
    print(f"\n[INFO] Querying Windows Task Scheduler task...")
    print(f"[INFO] Task name: {TASK_NAME}")
    
    command = [
        "schtasks",
        "/query",
        "/tn", TASK_NAME,
        "/v",  # 详细信息
        "/fo", "LIST"  # 列表格式
    ]
    
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"\n[OK] Task '{TASK_NAME}' found:")
            print(result.stdout)
            return True
        else:
            print(f"\n[WARN] Task '{TASK_NAME}' not found or error:")
            print(f"  {result.stderr}")
            return False
            
    except Exception as e:
        print(f"\n[ERROR] Exception: {e}")
        return False

def run_task():
    """手动运行任务"""
    print(f"\n[INFO] Manually running task...")
    print(f"[INFO] Task name: {TASK_NAME}")
    
    command = [
        "schtasks",
        "/run",
        "/tn", TASK_NAME
    ]
    
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"\n[OK] Task '{TASK_NAME}' started!")
            print(f"[INFO] Check http://localhost:8000/metrics for metrics")
            return True
        else:
            print(f"\n[ERROR] Failed to run task:")
            print(f"  {result.stderr}")
            return False
            
    except Exception as e:
        print(f"\n[ERROR] Exception: {e}")
        return False

def end_task():
    """结束正在运行的任务"""
    print(f"\n[INFO] Ending task...")
    print(f"[INFO] Task name: {TASK_NAME}")
    
    command = [
        "schtasks",
        "/end",
        "/tn", TASK_NAME
    ]
    
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"\n[OK] Task '{TASK_NAME}' ended!")
            return True
        else:
            print(f"\n[ERROR] Failed to end task:")
            print(f"  {result.stderr}")
            return False
            
    except Exception as e:
        print(f"\n[ERROR] Exception: {e}")
        return False

def show_help():
    """显示帮助信息"""
    print("""
Windows 自动启动配置脚本

将 Prometheus 监控集成到 Windows 任务计划程序。

使用方式:
    python setup_autostart.py --install    安装自动启动任务
    python setup_autostart.py --uninstall  卸载自动启动任务
    python setup_autostart.py --status     查看任务状态
    python setup_autostart.py --run        手动运行任务
    python setup_autostart.py --end        结束正在运行的任务

说明:
    --install: 创建一个 Windows 任务计划程序任务，在系统启动时自动运行
               Prometheus 监控。任务以 SYSTEM 用户身份运行，具有最高权限。
    
    --uninstall: 删除已创建的任务计划程序任务。
    
    --status: 查询任务的当前状态和详细信息。
    
    --run: 手动启动任务（无需重启系统）。
    
    --end: 结束正在运行的任务。

示例:
    # 安装自动启动
    python setup_autostart.py --install
    
    # 查看状态
    python setup_autostart.py --status
    
    # 手动运行
    python setup_autostart.py --run
    
    # 卸载
    python setup_autostart.py --uninstall
""")

def main():
    parser = argparse.ArgumentParser(
        description="Windows Auto-start Configuration for Prometheus Monitor",
        add_help=False
    )
    
    parser.add_argument(
        "--install", "-i",
        action="store_true",
        help="Install auto-start task"
    )
    
    parser.add_argument(
        "--uninstall", "-u",
        action="store_true",
        help="Uninstall auto-start task"
    )
    
    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Query task status"
    )
    
    parser.add_argument(
        "--run", "-r",
        action="store_true",
        help="Manually run task"
    )
    
    parser.add_argument(
        "--end", "-e",
        action="store_true",
        help="End running task"
    )
    
    parser.add_argument(
        "--help", "-h",
        action="store_true",
        help="Show help"
    )
    
    args = parser.parse_args()
    
    if args.help:
        show_help()
        return 0
    
    print("\n" + "=" * 70)
    print("[INFO] Yunshu V2 Prometheus Auto-start Configuration")
    print("=" * 70)
    
    success = True
    
    if args.install:
        success = install_task()
    elif args.uninstall:
        success = uninstall_task()
    elif args.status:
        success = query_task()
    elif args.run:
        success = run_task()
    elif args.end:
        success = end_task()
    else:
        # 默认显示状态
        print("\n[INFO] No action specified. Showing current status...")
        success = query_task()
        print("\n[INFO] Available actions:")
        print("  --install    Install auto-start task")
        print("  --uninstall  Uninstall auto-start task")
        print("  --status     Query task status")
        print("  --run        Manually run task")
        print("  --end        End running task")
        print("  --help       Show detailed help")
    
    print("\n" + "=" * 70)
    
    return 0 if success else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)