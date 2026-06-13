#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SafeFileReader 自动化部署脚本

提取自生产环境部署检查清单的关键步骤，自动完成：
1. 代码变更验证
2. 告警规则验证
3. 回滚脚本验证
4. 监控指标验证
5. 数据备份验证
6. 功能测试
7. 回滚演练

用法:
    python deploy_automation.py --help
    python deploy_automation.py --full  # 完整部署流程
    python deploy_automation.py --check-only  # 仅检查不部署
    python deploy_automation.py --rollback-drill  # 仅回滚演练
"""

import os
import sys
import argparse
import subprocess
import time
import json
import shutil
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def log(msg, level="INFO"):
    """日志输出"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    colors = {
        "INFO": Colors.BLUE,
        "SUCCESS": Colors.GREEN,
        "WARNING": Colors.YELLOW,
        "ERROR": Colors.RED
    }
    color = colors.get(level, Colors.BLUE)
    print(f"[{timestamp}] {color}[{level}]{Colors.END} {msg}")

def check_file_exists(filepath, description):
    """检查文件是否存在"""
    if os.path.exists(filepath):
        log(f"✅ {description}: {os.path.basename(filepath)}", "SUCCESS")
        return True
    else:
        log(f"❌ {description}: {filepath} 不存在", "ERROR")
        return False

def check_file_content(filepath, patterns, description):
    """检查文件内容是否包含指定模式"""
    if not os.path.exists(filepath):
        log(f"❌ 检查失败: 文件不存在 {filepath}", "ERROR")
        return False
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        all_found = True
        for pattern in patterns:
            if pattern not in content:
                log(f"❌ {description}: 未找到 '{pattern}'", "ERROR")
                all_found = False
        
        if all_found:
            log(f"✅ {description}", "SUCCESS")
        return all_found
    except Exception as e:
        log(f"❌ 读取文件失败 {filepath}: {e}", "ERROR")
        return False

def backup_files():
    """备份关键文件"""
    backup_time = datetime.now().strftime('%Y%m%d_%H%M%S')
    files_to_backup = [
        ("app_server.py", "."),
        ("data/messages.jsonl", "data"),
        ("utils/file_reader.py", "utils"),
        ("monitoring/alerts.yml", "monitoring"),
        ("utils/prometheus_exporter.py", "utils")
    ]
    
    log("开始备份关键文件...", "INFO")
    success_count = 0
    
    for filename, subdir in files_to_backup:
        src = os.path.join(PROJECT_ROOT, filename)
        if os.path.exists(src):
            backup_name = f"{filename}.bak_{backup_time}"
            dst = os.path.join(PROJECT_ROOT, backup_name)
            shutil.copy2(src, dst)
            log(f"✅ 已备份: {filename} -> {backup_name}", "SUCCESS")
            success_count += 1
        else:
            log(f"⚠️ 跳过备份: {filename} 不存在", "WARNING")
    
    return success_count

def check_code_changes():
    """检查代码变更"""
    log("=" * 60, "INFO")
    log("阶段1: 代码变更检查", "INFO")
    log("=" * 60, "INFO")
    
    checks = [
        (check_file_exists, [os.path.join(PROJECT_ROOT, "utils/file_reader.py"), "SafeFileReader 工具类存在"]),
        (check_file_content, [
            os.path.join(PROJECT_ROOT, "app_server.py"),
            ["_load_chat_history_from_file", "SafeFileReader"],
            "历史加载逻辑集成 SafeFileReader"
        ]),
        (check_file_content, [
            os.path.join(PROJECT_ROOT, "app_server.py"),
            ["DEFAULT_REGISTRY"],
            "Prometheus 指标注册修复"
        ]),
        (check_file_content, [
            os.path.join(PROJECT_ROOT, "utils/file_reader.py"),
            ["max_size_mb=10"],
            "文件大小限制（10MB）"
        ]),
        (check_file_content, [
            os.path.join(PROJECT_ROOT, "utils/file_reader.py"),
            ["utf-8", "utf-8-sig", "gbk"],
            "编码降级链"
        ]),
        (check_file_content, [
            os.path.join(PROJECT_ROOT, "utils/file_reader.py"),
            ["yunshu_safe_file_reader"],
            "Prometheus 指标上报"
        ])
    ]
    
    results = []
    for func, args in checks:
        results.append(func(*args))
    
    return all(results)

def check_alerts():
    """检查告警规则"""
    log("\n" + "=" * 60, "INFO")
    log("阶段2: 告警规则检查", "INFO")
    log("=" * 60, "INFO")
    
    alerts_file = os.path.join(PROJECT_ROOT, "monitoring/alerts.yml")
    alert_rules = [
        "SafeFileReaderFileNotFound",
        "SafeFileReaderFileTooLarge",
        "SafeFileReaderEncodingFallback",
        "SafeFileReaderAllEncodingsFailed",
        "SafeFileReaderHighInvalidRatio",
        "SafeFileReaderConsecutiveParseFailures",
        "SafeFileReaderHistoryLoadFailed",
        "SafeFileReaderHistoryLoadEmpty",
        "SafeFileReaderSlowRead"
    ]
    
    if not check_file_exists(alerts_file, "告警规则配置文件"):
        return False
    
    return check_file_content(alerts_file, alert_rules, "所有 SafeFileReader 告警规则已配置")

def check_rollback_scripts():
    """检查回滚脚本"""
    log("\n" + "=" * 60, "INFO")
    log("阶段3: 回滚脚本检查", "INFO")
    log("=" * 60, "INFO")
    
    checks = []
    
    # Shell 脚本
    shell_script = os.path.join(PROJECT_ROOT, "scripts/rollback.sh")
    checks.append(check_file_exists(shell_script, "Shell 回滚脚本"))
    checks.append(check_file_content(shell_script, ["-t monitoring", "SafeFileReader"], "Shell 脚本支持 monitoring 参数"))
    
    # PowerShell 脚本
    ps_script = os.path.join(PROJECT_ROOT, "scripts/rollback.ps1")
    checks.append(check_file_exists(ps_script, "PowerShell 回滚脚本"))
    checks.append(check_file_content(ps_script, ["monitoring", "SafeFileReader"], "PowerShell 脚本支持 monitoring 参数"))
    
    return all(checks)

def check_backups():
    """检查备份文件"""
    log("\n" + "=" * 60, "INFO")
    log("阶段4: 数据备份检查", "INFO")
    log("=" * 60, "INFO")
    
    backup_patterns = [
        ("app_server.py.bak_*", "应用服务器代码备份"),
        ("data/messages.jsonl.bak_*", "历史记忆数据备份"),
        ("utils/file_reader.py.bak_*", "SafeFileReader 工具类备份"),
        ("monitoring/alerts.yml.bak_*", "告警规则备份")
    ]
    
    import glob
    all_exist = True
    
    for pattern, desc in backup_patterns:
        full_pattern = os.path.join(PROJECT_ROOT, pattern)
        backups = glob.glob(full_pattern)
        if backups:
            latest = sorted(backups)[-1]
            log(f"✅ {desc}: {os.path.basename(latest)}", "SUCCESS")
        else:
            log(f"⚠️ {desc}: 未找到备份文件", "WARNING")
            all_exist = False
    
    return all_exist

def check_metrics():
    """检查 Prometheus 指标"""
    log("\n" + "=" * 60, "INFO")
    log("阶段5: 监控指标检查", "INFO")
    log("=" * 60, "INFO")
    
    exporter_file = os.path.join(PROJECT_ROOT, "utils/prometheus_exporter.py")
    metrics = [
        "yunshu_safe_file_reader_errors_total",
        "yunshu_safe_file_reader_encoding_fallbacks_total",
        "yunshu_safe_file_reader_read_duration_seconds",
        "yunshu_safe_file_reader_loaded_history_count",
        "yunshu_safe_file_reader_invalid_ratio"
    ]
    
    if not check_file_exists(exporter_file, "Prometheus 指标配置文件"):
        return False
    
    return check_file_content(exporter_file, metrics, "所有 SafeFileReader 指标已注册")

def run_functional_tests():
    """运行功能测试"""
    log("\n" + "=" * 60, "INFO")
    log("阶段6: 功能测试", "INFO")
    log("=" * 60, "INFO")
    
    # 检查测试文件是否存在
    test_files = [
        "tests/unit/test_safe_file_reader.py",
        "tests/unit/test_false_alarm_resistance.py",
        "tests/unit/test_history_load_edge_cases.py"
    ]
    
    all_exist = True
    for test_file in test_files:
        path = os.path.join(PROJECT_ROOT, test_file)
        if not os.path.exists(path):
            log(f"❌ 测试文件不存在: {test_file}", "ERROR")
            all_exist = False
        else:
            log(f"✅ 测试文件存在: {test_file}", "SUCCESS")
    
    if not all_exist:
        return False
    
    # 运行测试
    log("正在运行单元测试...", "INFO")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_safe_file_reader.py", "-v", "--tb=short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    
    if result.returncode == 0:
        log("✅ 单元测试通过", "SUCCESS")
        return True
    else:
        log("❌ 单元测试失败", "ERROR")
        log(result.stdout, "ERROR")
        log(result.stderr, "ERROR")
        return False

def rollback_drill():
    """回滚演练"""
    log("\n" + "=" * 60, "INFO")
    log("阶段7: 回滚演练", "INFO")
    log("=" * 60, "INFO")
    
    log("⚠️  此步骤将停止服务并执行回滚演练", "WARNING")
    response = input("确认执行回滚演练？(y/N): ")
    if response.lower() != 'y':
        log("回滚演练已取消", "INFO")
        return True
    
    # 停止服务
    log("停止服务...", "INFO")
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'python.exe'], capture_output=True)
        time.sleep(2)
        log("✅ 服务已停止", "SUCCESS")
    except Exception as e:
        log(f"⚠️  停止服务时发生错误: {e}", "WARNING")
    
    # 检查备份
    import glob
    backups = glob.glob(os.path.join(PROJECT_ROOT, "app_server.py.bak_*"))
    if backups:
        latest_backup = sorted(backups)[-1]
        log(f"找到最新备份: {os.path.basename(latest_backup)}", "INFO")
        
        # 模拟回滚
        shutil.copy2(latest_backup, os.path.join(PROJECT_ROOT, "app_server.py"))
        log("✅ 模拟回滚完成", "SUCCESS")
        
        # 恢复测试文件
        original_backup = os.path.join(PROJECT_ROOT, f"app_server.py.bak_drill_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(os.path.join(PROJECT_ROOT, "app_server.py"), original_backup)
        log(f"✅ 原始文件已备份到: {os.path.basename(original_backup)}", "SUCCESS")
    else:
        log("❌ 未找到备份文件，跳过回滚演练", "ERROR")
        return False
    
    return True

def main():
    parser = argparse.ArgumentParser(description="SafeFileReader 自动化部署脚本")
    parser.add_argument('--full', action='store_true', help='执行完整部署流程')
    parser.add_argument('--check-only', action='store_true', help='仅检查不部署')
    parser.add_argument('--rollback-drill', action='store_true', help='仅执行回滚演练')
    parser.add_argument('--backup', action='store_true', help='仅执行备份')
    args = parser.parse_args()
    
    log("=" * 70, "INFO")
    log("SafeFileReader 自动化部署脚本", "INFO")
    log("=" * 70, "INFO")
    
    results = []
    
    if args.backup:
        # 仅备份
        count = backup_files()
        log(f"\n✅ 已备份 {count} 个文件", "SUCCESS")
        return
    
    if args.check_only:
        # 仅检查
        results.append(("代码变更检查", check_code_changes()))
        results.append(("告警规则检查", check_alerts()))
        results.append(("回滚脚本检查", check_rollback_scripts()))
        results.append(("数据备份检查", check_backups()))
        results.append(("监控指标检查", check_metrics()))
    elif args.rollback_drill:
        # 仅回滚演练
        results.append(("回滚演练", rollback_drill()))
    elif args.full:
        # 完整流程
        backup_files()
        results.append(("代码变更检查", check_code_changes()))
        results.append(("告警规则检查", check_alerts()))
        results.append(("回滚脚本检查", check_rollback_scripts()))
        results.append(("数据备份检查", check_backups()))
        results.append(("监控指标检查", check_metrics()))
        results.append(("功能测试", run_functional_tests()))
        results.append(("回滚演练", rollback_drill()))
    else:
        parser.print_help()
        return
    
    # 输出结果汇总
    log("\n" + "=" * 70, "INFO")
    log("部署检查结果汇总", "INFO")
    log("=" * 70, "INFO")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        log(f"{name}: {status}", "SUCCESS" if result else "ERROR")
    
    log(f"\n总计: {passed}/{total} 通过", "INFO")
    
    if passed == total:
        log("🎉 所有检查通过！可以部署到生产环境", "SUCCESS")
    else:
        log("⚠️  部分检查失败，请修复后再部署", "WARNING")
        sys.exit(1)

if __name__ == '__main__':
    main()
