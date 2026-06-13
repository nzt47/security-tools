#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SafeFileReader 告警规则抗干扰能力测试

测试场景：
1. 首次运行空文件（不应触发告警）
2. 临时写入异常数据后恢复（告警应自动恢复）
3. 单条损坏行（不应触发连续失败告警）
4. 短暂网络波动（不应触发告警）
"""

import os
import sys
import time
import shutil
import subprocess
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MESSAGES_FILE = os.path.join(DATA_DIR, "messages.jsonl")
BACKUP_FILE = os.path.join(DATA_DIR, "messages.jsonl.bak_false_alarm_test")
SERVER_URL = "http://127.0.0.1:5678"

def log(msg, type="INFO"):
    print(f"[{type}] {msg}")

def backup_original():
    """备份原始文件"""
    if os.path.exists(MESSAGES_FILE):
        shutil.copy2(MESSAGES_FILE, BACKUP_FILE)
        log(f"已备份原始文件: {BACKUP_FILE}")
    else:
        log("原始文件不存在，跳过备份", "WARN")

def restore_original():
    """恢复原始文件"""
    if os.path.exists(BACKUP_FILE):
        shutil.copy2(BACKUP_FILE, MESSAGES_FILE)
        os.remove(BACKUP_FILE)
        log("已恢复原始文件")
    else:
        log("无备份文件，跳过恢复", "WARN")

def stop_server():
    """停止服务"""
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = ' '.join(p.info.get('cmdline') or [])
                if 'app_server.py' in cmdline and p.info['pid'] != os.getpid():
                    p.kill()
                    log(f"已停止服务 (PID: {p.info['pid']})")
            except:
                pass
    except ImportError:
        subprocess.run('taskkill /F /IM python.exe 2>nul', shell=True)
    time.sleep(2)

def start_server():
    """启动服务"""
    env = os.environ.copy()
    env['YUNSHU_FEATURE_SANDBOX'] = 'false'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    
    # 使用subprocess.PIPE会导致输出缓冲，改用文件重定向
    log_file = os.path.join(PROJECT_ROOT, 'logs', 'test_server.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    proc = subprocess.Popen(
        [sys.executable, '-u', 'app_server.py'],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=open(log_file, 'w', encoding='utf-8'),
        stderr=subprocess.STDOUT,
        text=True,
        shell=False
    )
    log(f"服务已启动 (PID: {proc.pid})，日志: {log_file}")
    
    # 等待服务就绪（增加超时时间）
    for i in range(60):
        time.sleep(1)
        try:
            r = requests.get(f"{SERVER_URL}/api/health", timeout=5)
            if r.status_code == 200:
                log(f"服务就绪 (耗时 {i+1}s)")
                return proc
        except requests.RequestException:
            # 检查进程是否还活着
            if proc.poll() is not None:
                log(f"服务进程已退出 (exit code: {proc.returncode})", "ERROR")
                return None
    
    log("服务启动超时", "ERROR")
    return None

def check_metrics():
    """检查 Prometheus 指标"""
    try:
        resp = requests.get(f"{SERVER_URL}/metrics", timeout=5)
        if resp.status_code != 200:
            return None
        
        metrics = {}
        for line in resp.text.split('\n'):
            if 'yunshu_safe_file_reader' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    metrics[parts[0]] = parts[1]
        return metrics
    except Exception as e:
        log(f"获取指标失败: {e}", "ERROR")
        return None

def scenario_1_empty_file():
    """场景1: 首次运行空文件 - 不应触发告警"""
    log("=" * 60)
    log("场景1: 首次运行空文件")
    log("=" * 60)
    
    stop_server()
    
    # 创建空文件
    open(MESSAGES_FILE, 'w').close()
    log("已创建空文件")
    
    proc = start_server()
    if not proc:
        log("场景1失败: 服务启动失败", "ERROR")
        return False
    
    time.sleep(5)
    metrics = check_metrics()
    
    if not metrics:
        log("场景1失败: 无法获取指标", "ERROR")
        return False
    
    # 检查指标
    json_failed = float(metrics.get('yunshu_safe_file_reader_errors_total', '0'))
    history_count = float(metrics.get('yunshu_safe_file_reader_loaded_history_count', '0'))
    
    log(f"json_parse_failed: {json_failed}")
    log(f"loaded_history_count: {history_count}")
    
    # 判断结果
    if json_failed == 0 and history_count == 0:
        log("✅ 场景1通过: 空文件不应触发告警，当前状态正常")
        log("   - json_parse_failed = 0（无错误）")
        log("   - loaded_history_count = 0（空文件正常）")
        result = True
    else:
        log("❌ 场景1失败: 空文件触发了不必要的告警", "ERROR")
        result = False
    
    stop_server()
    return result

def scenario_2_temporary_corruption():
    """场景2: 临时写入异常数据后恢复 - 告警应自动恢复"""
    log("\n" + "=" * 60)
    log("场景2: 临时写入异常数据后恢复")
    log("=" * 60)
    
    # 第一步: 写入损坏数据
    stop_server()
    
    with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
        f.write('{"broken json line {{{{\n' * 15)
    log("已写入15条损坏数据")
    
    proc = start_server()
    if not proc:
        log("场景2失败: 服务启动失败", "ERROR")
        return False
    
    time.sleep(5)
    metrics = check_metrics()
    
    if not metrics:
        log("场景2失败: 无法获取指标", "ERROR")
        return False
    
    # 记录损坏时的指标
    json_failed_before = float(metrics.get('yunshu_safe_file_reader_errors_total', '0'))
    log(f"损坏时 json_parse_failed: {json_failed_before}")
    
    if json_failed_before < 10:
        log("⚠️  警告: 损坏数据未正确记录", "WARN")
    
    # 第二步: 恢复正常数据
    stop_server()
    
    # 恢复备份（如果存在）或创建正常数据
    if os.path.exists(BACKUP_FILE):
        shutil.copy2(BACKUP_FILE, MESSAGES_FILE)
    else:
        # 创建正常的测试数据
        with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
            f.write('{"role": "user", "content": "test message"}\n')
    
    log("已恢复正常数据")
    
    # 重启服务
    proc = start_server()
    if not proc:
        log("场景2失败: 恢复后服务启动失败", "ERROR")
        return False
    
    time.sleep(5)
    metrics = check_metrics()
    
    if not metrics:
        log("场景2失败: 恢复后无法获取指标", "ERROR")
        return False
    
    # 检查恢复后的指标
    history_count_after = float(metrics.get('yunshu_safe_file_reader_loaded_history_count', '0'))
    
    log(f"恢复后 loaded_history_count: {history_count_after}")
    
    if history_count_after > 0:
        log("✅ 场景2通过: 恢复后告警应自动清除")
        log("   - 服务正常启动")
        log("   - 历史记录正确加载")
        result = True
    else:
        log("❌ 场景2失败: 恢复后历史仍未加载", "ERROR")
        result = False
    
    stop_server()
    return result

def scenario_3_single_bad_line():
    """场景3: 单条损坏行 - 不应触发连续失败告警"""
    log("\n" + "=" * 60)
    log("场景3: 单条损坏行")
    log("=" * 60)
    
    stop_server()
    
    # 创建混合数据（1条损坏 + 2条正常）
    with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
        f.write('{"role": "user", "content": "normal message 1"}\n')
        f.write('{"broken json {{{{\n')
        f.write('{"role": "assistant", "content": "normal message 2"}\n')
    
    log("已创建混合数据（1条损坏 + 2条正常）")
    
    proc = start_server()
    if not proc:
        log("场景3失败: 服务启动失败", "ERROR")
        return False
    
    time.sleep(5)
    metrics = check_metrics()
    
    if not metrics:
        log("场景3失败: 无法获取指标", "ERROR")
        return False
    
    json_failed = float(metrics.get('yunshu_safe_file_reader_errors_total', '0'))
    history_count = float(metrics.get('yunshu_safe_file_reader_loaded_history_count', '0'))
    invalid_ratio = float(metrics.get('yunshu_safe_file_reader_invalid_ratio', '0'))
    
    log(f"json_parse_failed: {json_failed}")
    log(f"loaded_history_count: {history_count}")
    log(f"invalid_ratio: {invalid_ratio}")
    
    # 判断结果
    passed = True
    
    if json_failed == 1:
        log("✅ 单条损坏行正确计数")
    else:
        log(f"❌ 损坏行计数错误: {json_failed} (预期: 1)", "ERROR")
        passed = False
    
    if invalid_ratio < 0.1:  # < 10%，不应触发告警
        log("✅ 无效比例 < 10%，不应触发告警")
    else:
        log(f"❌ 无效比例过高: {invalid_ratio:.1%}", "ERROR")
        passed = False
    
    if passed:
        log("✅ 场景3通过: 单条损坏行不应触发告警")
    
    stop_server()
    return passed

def scenario_4_network_fluctuation():
    """场景4: 短暂网络波动 - 不应触发告警"""
    log("\n" + "=" * 60)
    log("场景4: 短暂网络波动模拟")
    log("=" * 60)
    
    # 网络波动场景主要验证:
    # 1. 服务重启后指标不会累积（每次启动重置）
    # 2. 短暂中断不会触发持续告警
    
    stop_server()
    
    # 使用正常文件启动
    if os.path.exists(BACKUP_FILE):
        shutil.copy2(BACKUP_FILE, MESSAGES_FILE)
    
    proc = start_server()
    if not proc:
        log("场景4失败: 服务启动失败", "ERROR")
        return False
    
    time.sleep(3)
    
    # 模拟网络波动（快速重启）
    stop_server()
    time.sleep(1)
    
    proc = start_server()
    if not proc:
        log("场景4失败: 重启后服务启动失败", "ERROR")
        return False
    
    time.sleep(5)
    metrics = check_metrics()
    
    if not metrics:
        log("场景4失败: 无法获取指标", "ERROR")
        return False
    
    json_failed = float(metrics.get('yunshu_safe_file_reader_errors_total', '0'))
    
    log(f"快速重启后 json_parse_failed: {json_failed}")
    
    if json_failed == 0:
        log("✅ 场景4通过: 短暂网络波动后服务正常")
        result = True
    else:
        log("❌ 场景4失败: 网络波动导致错误计数", "ERROR")
        result = False
    
    stop_server()
    return result

def main():
    log("=" * 70)
    log("SafeFileReader 告警规则抗干扰能力测试")
    log("=" * 70)
    log("")
    
    # 备份原始文件
    backup_original()
    
    try:
        results = []
        
        # 场景1: 空文件
        results.append(("场景1: 空文件容错", scenario_1_empty_file()))
        
        # 场景2: 临时损坏后恢复
        results.append(("场景2: 临时损坏后恢复", scenario_2_temporary_corruption()))
        
        # 场景3: 单条损坏行
        results.append(("场景3: 单条损坏行", scenario_3_single_bad_line()))
        
        # 场景4: 网络波动
        results.append(("场景4: 网络波动", scenario_4_network_fluctuation()))
        
        # 汇总结果
        log("\n" + "=" * 70)
        log("测试结果汇总")
        log("=" * 70)
        
        passed = sum(1 for _, r in results if r)
        total = len(results)
        
        for name, result in results:
            status = "✅ 通过" if result else "❌ 失败"
            log(f"{name}: {status}")
        
        log("")
        log(f"总计: {passed}/{total} 通过")
        
        if passed == total:
            log("🎉 所有测试通过！告警规则具备良好的抗干扰能力")
        else:
            log("⚠️  部分测试失败，请检查告警规则配置", "WARN")
        
    finally:
        # 恢复原始文件
        restore_original()
        
        # 确保服务停止
        stop_server()

if __name__ == '__main__':
    main()
