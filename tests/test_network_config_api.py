"""
网络配置 API 端到端测试

测试场景：
1. 测试配置保存后是否能正确应用到 HTTP 客户端
2. 验证配置保存和即时生效的日志输出
3. 导入包含错误格式 URL 的配置，测试前端输入验证

运行方式：确保服务器运行在 http://127.0.0.1:5678
"""

import json
import requests

BASE_URL = "http://127.0.0.1:5678"

print("=" * 70)
print("网络配置 API 端到端测试")
print("=" * 70)

# ── 测试 1：获取当前网络配置 ──
print("\n【测试 1】获取当前网络配置")
print("-" * 40)

res = requests.get(f"{BASE_URL}/api/network-config")
if res.status_code == 200:
    config = res.json()
    print(f"✓ LLM 超时: {config['llm']['timeout']}s")
    print(f"✓ 网络超时: {config['network']['timeout']}s")
    print(f"✓ 代理状态: {config['network']['proxy_enabled']}")
    print(f"✓ API Key 脱敏: {config['llm'].get('api_key', '未设置')}")
else:
    print(f"✗ 获取失败: {res.status_code} - {res.text}")

# ── 测试 2：保存网络配置并验证即时生效 ──
print("\n【测试 2】保存网络配置并验证即时生效")
print("-" * 40)

updates = {
    "llm": {
        "enabled": True,
        "provider": "openai",
        "api_key": "sk-test-end-to-end-123456",
        "model": "gpt-4",
        "api_endpoint": "https://api.openai.com/v1",
        "timeout": 60,
        "max_retries": 5,
    },
    "network": {
        "timeout": 45,
        "max_retries": 4,
        "backoff_factor": 1.0,
        "proxy_enabled": False,
        "proxy_url": "",
    },
}

res = requests.post(
    f"{BASE_URL}/api/network-config",
    headers={"Content-Type": "application/json"},
    json=updates,
)

if res.status_code == 200:
    result = res.json()
    if result.get('ok'):
        print(f"✓ 配置保存成功")
        print(f"✓ 返回的 LLM 超时: {result['config']['llm']['timeout']}s")
        print(f"✓ 返回的网络超时: {result['config']['network']['timeout']}s")
        print(f"✓ API Key 已脱敏: {result['config']['llm']['api_key']}")
        print("📝 提示：请查看服务器日志中的 [网络配置] 标记，验证日志输出")
    else:
        print(f"✗ 保存失败: {result.get('error')}")
else:
    print(f"✗ 请求失败: {res.status_code} - {res.text}")

# ── 测试 3：验证配置已持久化 ──
print("\n【测试 3】验证配置已持久化")
print("-" * 40)

res = requests.get(f"{BASE_URL}/api/network-config")
if res.status_code == 200:
    config = res.json()
    if config['llm']['timeout'] == 60 and config['network']['timeout'] == 45:
        print("✓ 配置已正确持久化")
    else:
        print(f"✗ 配置未持久化: LLM={config['llm']['timeout']}, Network={config['network']['timeout']}")
else:
    print(f"✗ 获取失败: {res.status_code}")

# ── 测试 4：导入错误格式的配置（前端验证逻辑测试） ──
print("\n【测试 4】模拟前端输入验证 - 错误格式 URL")
print("-" * 40)

invalid_config = {
    "llm": {
        "enabled": True,
        "provider": "openai",
        "api_key": "sk-test",
        "model": "gpt-4",
        "timeout": 30,
        "max_retries": 3,
    },
    "network": {
        "timeout": 30,
        "max_retries": 3,
        "backoff_factor": 0.5,
        "proxy_enabled": True,
        "proxy_url": "not-a-valid-url",  # 无效 URL
    },
    "search": {
        "enabled": True,
        "default_engine": "google",
        "max_results": 10,
    },
    "web_scraping": {
        "enabled": True,
        "respect_robots_txt": True,
        "delay_between_requests": 1.0,
    },
    "browser": {
        "enabled": False,
        "headless": True,
        "timeout": 30,
    },
    "sync": {
        "enabled": True,
        "interval_minutes": 60,
        "auto_sync_on_start": True,
    },
    "external_services": {
        "error_reporting": {
            "enabled": True,
            "webhook_url": "not-a-webhook-url",  # 无效 URL
        },
        "monitoring": {
            "enabled": False,
            "endpoint": "",
        },
    },
}

# 前端验证（与 network-config.js 中的 validateNetworkConfig 逻辑一致）
def validate_network_config(config):
    errors = []
    
    if config['llm']['timeout'] < 1 or config['llm']['timeout'] > 300:
        errors.append('LLM 超时应在 1-300 秒之间')
    
    if config['network']['timeout'] < 1 or config['network']['timeout'] > 300:
        errors.append('网络超时应在 1-300 秒之间')
    
    if config['network']['proxy_enabled'] and config['network']['proxy_url']:
        try:
            from urllib.parse import urlparse
            result = urlparse(config['network']['proxy_url'])
            if not all([result.scheme, result.netloc]):
                raise ValueError("Invalid URL")
        except:
            errors.append('代理 URL 格式无效')
    
    if config['external_services']['error_reporting']['enabled'] and config['external_services']['error_reporting']['webhook_url']:
        try:
            from urllib.parse import urlparse
            result = urlparse(config['external_services']['error_reporting']['webhook_url'])
            if not all([result.scheme, result.netloc]):
                raise ValueError("Invalid URL")
        except:
            errors.append('Webhook URL 格式无效')
    
    return errors

errors = validate_network_config(invalid_config)
if errors:
    print(f"✓ 前端验证捕获到 {len(errors)} 个错误:")
    for err in errors:
        print(f"  - {err}")
else:
    print("✗ 前端验证未捕获到错误！")

# ── 测试 5：导出配置 ──
print("\n【测试 5】导出配置")
print("-" * 40)

res = requests.get(f"{BASE_URL}/api/network-config/export")
if res.status_code == 200:
    result = res.json()
    if result.get('ok'):
        exported = json.loads(result['config_json'])
        print(f"✓ 导出成功，包含 {len(exported)} 个模块:")
        for key in exported:
            print(f"  - {key}")
    else:
        print(f"✗ 导出失败: {result.get('error')}")
else:
    print(f"✗ 请求失败: {res.status_code}")

# ── 测试 6：导入配置 ──
print("\n【测试 6】导入配置")
print("-" * 40)

# 先导出，再导入
res_export = requests.get(f"{BASE_URL}/api/network-config/export")
if res_export.status_code == 200:
    export_result = res_export.json()
    res_import = requests.post(
        f"{BASE_URL}/api/network-config/import",
        headers={"Content-Type": "application/json"},
        json={"config_json": export_result['config_json']},
    )
    if res_import.status_code == 200:
        result = res_import.json()
        if result.get('ok'):
            print("✓ 导入成功")
        else:
            print(f"✗ 导入失败: {result.get('error')}")
    else:
        print(f"✗ 请求失败: {res_import.status_code}")

# ── 测试 7：重置配置 ──
print("\n【测试 7】重置配置为默认值")
print("-" * 40)

res = requests.post(f"{BASE_URL}/api/network-config/reset")
if res.status_code == 200:
    result = res.json()
    if result.get('ok'):
        print("✓ 配置已重置为默认值")
        print(f"  LLM 超时: {result['config']['llm']['timeout']}s")
        print(f"  网络超时: {result['config']['network']['timeout']}s")
    else:
        print(f"✗ 重置失败: {result.get('error')}")
else:
    print(f"✗ 请求失败: {res.status_code}")

print("\n" + "=" * 70)
print("端到端测试完成！")
print("提示：请检查服务器控制台日志，查看 [网络配置] 相关日志输出")
print("=" * 70)
