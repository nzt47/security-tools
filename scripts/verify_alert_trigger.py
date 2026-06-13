import json, shutil, os, subprocess, time, requests, sys

SRC = 'data/messages.jsonl'
BAK = 'data/messages.jsonl.bak_alert_test'
SERVER_URL = 'http://127.0.0.1:5678'

print("=" * 60)
print("🔔 Prometheus 告警触发验证 - 重启服务场景")
print("=" * 60)

# 1. 备份
print("\n步骤 1/5: 备份原始文件")
shutil.copy2(SRC, BAK)
print(f"✅ 已备份到 {BAK}")

# 2. 注入损坏行
print("\n步骤 2/5: 注入15条损坏行")
with open(SRC, 'r', encoding='utf-8') as f:
    lines = f.readlines()

with open(SRC, 'a', encoding='utf-8') as f:
    for i in range(15):
        f.write('{"broken json line %d {{{{\n' % i)

total = len(lines) + 15
print(f"✅ 已注入15条损坏行（总计 {total} 行）")

# 3. 重启服务
print("\n步骤 3/5: 启动服务（带损坏文件）")
env = os.environ.copy()
env['YUNSHU_FEATURE_SANDBOX'] = 'false'
env['PYTHONUNBUFFERED'] = '1'  # 禁用输出缓冲
proc = subprocess.Popen(
    [sys.executable, '-u', 'app_server.py'],  # -u 禁用缓冲
    cwd=os.getcwd(),
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,  # 合并到 stdout
    text=True,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
)
print(f"✅ 服务已启动 (PID: {proc.pid})")

# 4. 等待启动并检查日志
print("\n步骤 4/5: 等待服务启动，检查加载日志...")
import threading
log_output = []
def read_log():
    for line in proc.stdout:
        log_output.append(line)

reader_thread = threading.Thread(target=read_log, daemon=True)
reader_thread.start()

# 等待启动（最多 60 秒）
for i in range(60):
    time.sleep(1)
    try:
        r = requests.get(f"{SERVER_URL}/api/health", timeout=2)
        if r.status_code == 200:
            print(f"✅ 服务就绪 (耗时 {i+1}s)")
            break
    except:
        if (i+1) % 10 == 0:
            print(f"   等待中... ({i+1}s)")
        # 检查进程是否还活着
        if proc.poll() is not None:
            print(f"❌ 服务进程已退出 (exit code: {proc.poll()})")
            break

# 分析日志
log_text = ''.join(log_output)

print("\n" + "─" * 60)
print("📋 历史加载日志分析:")
print("─" * 60)

# 提取关键日志
alert_keywords = [
    ('历史加载] 开始', '📂'),
    ('历史加载] 文件路径', '📂'),
    ('历史加载] 文件大小', '📊'),
    ('历史加载] 文件读取完成', '✅'),
    ('历史加载] 配对完成', '✅'),
    ('历史加载] 最终加载', '✅'),
    ('JSON 解析失败', '⚠️'),
    ('第 ', '⚠️'),
    ('SafeFileReader', '📦'),
]

found_logs = []
for keyword, icon in alert_keywords:
    for line in log_text.split('\n'):
        if keyword in line:
            found_logs.append(f"  {icon} {line.strip()}")

for log in found_logs[:20]:
    print(log)

# 5. 检查 /metrics 端点
print("\n" + "─" * 60)
print("📈 Prometheus 指标检查:")
print("─" * 60)

try:
    resp = requests.get(f"{SERVER_URL}/metrics", timeout=5)
    if resp.status_code == 200:
        content = resp.text
        
        # 提取 SafeFileReader 相关指标
        for line in content.split('\n'):
            if 'yunshu_safe_file_reader' in line and not line.startswith('#'):
                print(f"  {line.strip()}")
        
        # 检查告警条件
        json_failed = 0
        for line in content.split('\n'):
            if 'yunshu_safe_file_reader_errors_total' in line and 'json_parse_failed' in line:
                try:
                    json_failed = int(float(line.split()[-1]))
                except:
                    pass
        
        print("\n" + "─" * 60)
        if json_failed > 10:
            print(f"🎉 告警触发验证成功！")
            print(f"   json_parse_failed = {json_failed} > 10 (阈值)")
            print(f"   告警规则: SafeFileReaderConsecutiveParseFailures")
            print(f"   状态: ✅ 满足触发条件")
        else:
            print(f"⚠️  告警条件未满足")
            print(f"   json_parse_failed = {json_failed} (阈值: 10)")
            print(f"   可能需要检查日志中的错误计数")
    else:
        print(f"❌ /metrics 返回 {resp.status_code}")
except Exception as e:
    print(f"❌ 无法访问 /metrics: {e}")

# 6. 恢复文件并停止服务
print("\n" + "─" * 60)
print("🔄 恢复原始文件...")
print("─" * 60)

shutil.copy2(BAK, SRC)
os.remove(BAK)
print("✅ 原始文件已恢复")

print("\n⚠️  服务仍在运行（PID: %d），包含正常数据" % proc.pid)
print("   如需停止: 在终端按 Ctrl+C 或运行 taskkill /PID %d /F" % proc.pid)

print("\n" + "=" * 60)
print("✅ 验证完成！")
print("=" * 60)
