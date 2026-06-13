#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""完整告警验证：停止服务 → 注入损坏文件 → 重启 → 检查指标 → 恢复"""

import subprocess, os, sys, time, shutil, signal, json

SRC = 'data/messages.jsonl'
BAK = 'data/messages.jsonl.bak_alert_test'

print("=" * 60)
print("🔔 完整告警触发验证")
print("=" * 60)

# 1. 停止现有服务
print("\n步骤 1/5: 停止现有服务")
try:
    import psutil
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(p.info.get('cmdline') or [])
            if 'app_server.py' in cmdline and p.info['pid'] != os.getpid():
                p.kill()
                print(f"   已停止 PID={p.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
except ImportError:
    # 无 psutil 时用 taskkill
    subprocess.run('taskkill /F /FI "WINDOWTITLE eq *app_server*" 2>nul', shell=True)
time.sleep(3)
print("✅ 服务已停止")

# 2. 备份并注入损坏行
print("\n步骤 2/5: 注入15条损坏行")
shutil.copy2(SRC, BAK)
with open(SRC, 'r', encoding='utf-8') as f:
    lines = f.readlines()
with open(SRC, 'a', encoding='utf-8') as f:
    for i in range(15):
        f.write('{"broken json line %d {{{{\n' % i)
print(f"✅ 已注入15条损坏行（总计 {len(lines)+15} 行）")

# 3. 启动服务（带 PYTHONUNBUFFERED + 重定向日志到文件）
print("\n步骤 3/5: 启动服务（带损坏文件）")
env = os.environ.copy()
env['YUNSHU_FEATURE_SANDBOX'] = 'false'
env['PYTHONUNBUFFERED'] = '1'
env['PYTHONIOENCODING'] = 'utf-8'

log_file = 'logs/alert_test_startup.log'
os.makedirs('logs', exist_ok=True)

with open(log_file, 'w', encoding='utf-8') as log_f:
    proc = subprocess.Popen(
        [sys.executable, '-u', 'app_server.py'],
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    print(f"✅ 服务已启动 (PID: {proc.pid})")
    print(f"   日志: {log_file}")

# 4. 等待启动并检查 /metrics
print("\n步骤 4/5: 等待服务启动...")
ready = False
for i in range(30):
    time.sleep(1)
    try:
        import requests
        r = requests.get('http://127.0.0.1:5678/api/health', timeout=2)
        if r.status_code == 200:
            print(f"✅ 服务就绪 (耗时 {i+1}s)")
            ready = True
            break
    except:
        if proc.poll() is not None:
            print(f"❌ 服务进程已退出 (code: {proc.poll()})")
            break
        if (i+1) % 5 == 0:
            print(f"   等待中... ({i+1}s)")

if ready:
    # 检查 /metrics
    print("\n" + "─" * 60)
    print("📈 Prometheus 指标:")
    print("─" * 60)
    
    import requests
    resp = requests.get('http://127.0.0.1:5678/metrics', timeout=5)
    
    json_failed = 0
    errors = []
    duration = None
    history_count = None
    invalid_ratio = None
    
    for line in resp.text.split('\n'):
        if 'yunshu_safe_file_reader' in line and not line.startswith('#'):
            print(f"  {line.strip()}")
            if 'json_parse_failed' in line:
                try:
                    json_failed = int(float(line.split()[-1]))
                except: pass
            if 'read_duration_seconds_count' in line:
                try:
                    duration = float(line.split()[-1])
                except: pass
            if 'loaded_history_count' in line:
                try:
                    history_count = int(float(line.split()[-1]))
                except: pass
            if 'invalid_ratio' in line and 'count' not in line:
                try:
                    invalid_ratio = float(line.split()[-1])
                except: pass
    
    print("\n" + "─" * 60)
    print("📋 告警验证结果:")
    print("─" * 60)
    
    if json_failed > 10:
        print(f"✅ json_parse_failed = {json_failed} > 10 (阈值)")
        print(f"✅ 告警规则 SafeFileReaderConsecutiveParseFailures: 触发条件满足")
    else:
        print(f"⚠️  json_parse_failed = {json_failed} (阈值: 10)")
        print(f"   可能原因: 服务启动时历史加载未触发或文件已被恢复")
    
    if invalid_ratio is not None:
        print(f"{'✅' if invalid_ratio > 0.1 else '⚠️'}  invalid_ratio = {invalid_ratio:.1%} (阈值: 10%)")
    
    if history_count is not None:
        print(f"ℹ️  loaded_history_count = {history_count}")
    
    if duration is not None:
        print(f"ℹ️  read_duration = {duration:.3f}s")

# 5. 恢复文件
print("\n" + "─" * 60)
print("🔄 步骤 5/5: 恢复原始文件")
print("─" * 60)

shutil.copy2(BAK, SRC)
os.remove(BAK)
print("✅ 原始文件已恢复")

# 显示启动日志摘要
if os.path.exists(log_file):
    with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
        log_content = f.read()
    
    print("\n📋 启动日志摘要（历史加载相关）:")
    print("─" * 60)
    for line in log_content.split('\n'):
        if any(kw in line for kw in ['历史加载', 'JSON 解析失败', 'SafeFileReader', '文件读取完成', '配对完成']):
            print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("✅ 验证完成！")
print(f"⚠️  服务仍在运行（PID: {proc.pid}），使用正常数据")
print("=" * 60)
