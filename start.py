#!/usr/bin/env python3
"""
云枢 V2 启动脚本
提供多种启动选项，包括：
- 普通模式（无监控）
- 性能监控模式
- Prometheus 指标导出模式
- 诊断模式
- 监控堆栈启动模式
"""

import sys
import os
import argparse
import time
import logging
import subprocess
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def run_diagnostics():
    """运行诊断模式"""
    print("\n" + "=" * 70)
    print("[DIAGNOSE] 诊断模式")
    print("=" * 70)
    try:
        result = subprocess.run(
            [sys.executable, str(project_root / "diagnose_v2.py")],
            check=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"诊断失败: {e}")
        return False


def run_tests():
    """运行完整测试套件"""
    print("\n" + "=" * 70)
    print("[TEST] 测试模式")
    print("=" * 70)
    try:
        result = subprocess.run(
            [sys.executable, str(project_root / "run_all_tests.py")],
            check=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"测试失败: {e}")
        return False


def run_prometheus_monitor():
    """运行 Prometheus 指标导出模式"""
    print("\n" + "=" * 70)
    print("[PROMETHEUS] Prometheus 指标导出模式")
    print("=" * 70)
    
    # 检查 prometheus_client 是否已安装
    try:
        import prometheus_client
        try:
            version = prometheus_client.__version__
            print(f"Prometheus Client 版本: {version}")
        except AttributeError:
            print("Prometheus Client 已安装")
    except ImportError:
        print("\n提示: 请先安装 prometheus_client 库")
        print("  pip install prometheus_client")
        return False
    
    try:
        print("\n正在启动 Prometheus 指标导出...")
        print("指标将在 http://localhost:8000/metrics 导出")
        print("按 Ctrl+C 停止\n")
        
        result = subprocess.run(
            [sys.executable, str(project_root / "prometheus_example.py")],
            check=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Prometheus 监控启动失败: {e}")
        return False


def run_monitoring_stack():
    """运行完整监控堆栈（Prometheus + Grafana）"""
    print("\n" + "=" * 70)
    print("[STACK] 完整监控堆栈模式")
    print("=" * 70)
    
    # 检查 Docker 是否可用
    try:
        docker_result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True
        )
        if docker_result.returncode != 0:
            print("Docker 未安装或不可用")
            return False
        print(f"Docker 版本: {docker_result.stdout.strip()}")
    except FileNotFoundError:
        print("Docker 未安装")
        print("\n提示: 请先安装 Docker Desktop")
        return False
    
    monitoring_dir = project_root / "monitoring"
    
    # 检查操作系统
    if os.name == "nt":  # Windows
        script_path = monitoring_dir / "start_monitoring.ps1"
        print("\nWindows 系统，使用 PowerShell 脚本")
        try:
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
                cwd=str(monitoring_dir),
                check=True
            )
            return result.returncode == 0
        except Exception as e:
            print(f"监控堆栈启动失败: {e}")
            return False
    else:  # Linux/Mac
        script_path = monitoring_dir / "start_monitoring.sh"
        print("\nLinux/Mac 系统，使用 Bash 脚本")
        try:
            result = subprocess.run(
                ["bash", str(script_path)],
                cwd=str(monitoring_dir),
                check=True
            )
            return result.returncode == 0
        except Exception as e:
            print(f"监控堆栈启动失败: {e}")
            return False


def run_normal_mode():
    """运行普通模式（无监控）"""
    print("\n" + "=" * 70)
    print("[NORMAL] 普通模式")
    print("=" * 70)
    
    try:
        from agent.digital_life import DigitalLife
        dl = DigitalLife()
        print("\n[OK] 云枢已启动!")
        print("\n可用工具:")
        print("  - chat(): 与云枢对话")
        print("  - get_status(): 获取完整状态")
        print("  - get_performance_report(): 获取性能报告")
        print("\n按 Ctrl+C 退出")
        
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n[BYE] 再见!")
        return True
    except Exception as e:
        print(f"启动失败: {e}")
        return False


def show_help():
    """显示帮助信息"""
    print("""
云枢 V2 启动脚本

使用方法:
  python start.py [选项]

选项:
  -h, --help          显示帮助信息
  -d, --diagnose      运行诊断模式
  -t, --test          运行完整测试套件
  -p, --prometheus    启动 Prometheus 指标导出模式
  -s, --stack         启动完整监控堆栈（Prometheus + Grafana）
  -n, --normal        普通模式（无监控，默认）
  -a, --all           诊断 -> 测试 -> Prometheus（顺序执行）

示例:
  python start.py                    # 普通模式
  python start.py -d                 # 诊断模式
  python start.py -t                 # 运行测试
  python start.py -p                 # Prometheus 指标导出
  python start.py -s                 # 监控堆栈
  python start.py -a                 # 完整流程
""")


def main():
    parser = argparse.ArgumentParser(
        description="云枢 V2 启动脚本",
        add_help=False
    )
    
    parser.add_argument(
        "-h", "--help",
        action="store_true",
        help="显示帮助信息"
    )
    
    parser.add_argument(
        "-d", "--diagnose",
        action="store_true",
        help="运行诊断模式"
    )
    
    parser.add_argument(
        "-t", "--test",
        action="store_true",
        help="运行完整测试套件"
    )
    
    parser.add_argument(
        "-p", "--prometheus",
        action="store_true",
        help="启动 Prometheus 指标导出模式"
    )
    
    parser.add_argument(
        "-s", "--stack",
        action="store_true",
        help="启动完整监控堆栈（Prometheus + Grafana）"
    )
    
    parser.add_argument(
        "-n", "--normal",
        action="store_true",
        help="普通模式（无监控）"
    )
    
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="诊断 -> 测试 -> Prometheus（顺序执行）"
    )
    
    args = parser.parse_args()
    
    if args.help:
        show_help()
        return 0
    
    success = True
    
    if args.all:
        print("\n" + "=" * 70)
        print("[ALL] 完整流程：诊断 -> 测试 -> Prometheus 指标导出")
        print("=" * 70)
        
        # 1. 诊断
        if not run_diagnostics():
            print("\n[ERROR] 诊断失败，终止流程")
            return 1
        
        # 2. 测试
        print("\n" + "=" * 70)
        print("等待 2 秒...")
        print("=" * 70)
        time.sleep(2)
        
        if not run_tests():
            print("\n[ERROR] 测试失败，终止流程")
            return 1
        
        # 3. Prometheus 指标导出
        print("\n" + "=" * 70)
        print("等待 2 秒...")
        print("=" * 70)
        time.sleep(2)
        
        print("\n启动 Prometheus 指标导出...")
        print("提示: 此模式将持续运行，按 Ctrl+C 退出")
        time.sleep(1)
        return run_prometheus_monitor()
    
    elif args.diagnose:
        success = run_diagnostics()
    elif args.test:
        success = run_tests()
    elif args.prometheus:
        success = run_prometheus_monitor()
    elif args.stack:
        success = run_monitoring_stack()
    elif args.normal:
        success = run_normal_mode()
    else:
        # 默认运行普通模式
        success = run_normal_mode()
    
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n[BYE] 再见!")
        sys.exit(0)
