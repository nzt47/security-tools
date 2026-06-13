"""
网络配置模块测试脚本

测试场景：
1. 模拟 API 响应，测试配置保存后是否正确应用到 HTTP 客户端
2. 验证配置保存和即时生效的日志输出
3. 导入包含错误格式 URL 的配置，测试前端输入验证
"""

import json
import sys
import os

# 确保项目根目录在 sys.path 中（agent 是子包）
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agent.network_config import NetworkConfigManager

print("=" * 70)
print("网络配置模块测试")
print("=" * 70)

# ── 测试 1：配置保存和即时生效 ──
print("\n【测试 1】配置保存和即时生效")
print("-" * 40)

mgr = NetworkConfigManager()

# 获取当前配置
config = mgr.get_all()
print(f"✓ 当前 LLM 超时: {config['llm']['timeout']}s")
print(f"✓ 当前网络超时: {config['network']['timeout']}s")
print(f"✓ 当前代理状态: {config['network']['proxy_enabled']}")

# 更新配置
updates = {
    "llm": {
        "enabled": True,
        "provider": "openai",
        "api_key": "sk-test-1234567890abcdef",
        "model": "gpt-4",
        "api_endpoint": "https://api.openai.com/v1",
        "timeout": 60,
        "max_retries": 5,
    },
    "network": {
        "timeout": 45,
        "max_retries": 4,
        "backoff_factor": 1.0,
        "proxy_enabled": True,
        "proxy_url": "http://proxy.example.com:8080",
    },
    "sync": {
        "interval_minutes": 120,
    },
}

print("\n正在更新配置...")
result = mgr.update(updates)
print(f"✓ 配置已更新，返回的 LLM 超时: {result['llm']['timeout']}s")
print(f"✓ 返回的网络超时: {result['network']['timeout']}s")
print(f"✓ 返回的 API Key 脱敏: {result['llm']['api_key']}")

# 验证敏感信息已脱敏
assert result['llm']['api_key'].startswith('***'), "API Key 未脱敏！"
print("✓ API Key 脱敏验证通过")

# ── 测试 2：模拟应用实例（Mock） ──
print("\n【测试 2】模拟配置应用到 HTTP 客户端")
print("-" * 40)

class MockHttpClient:
    def __init__(self):
        self.timeout = 30
        self.max_retries = 3

class MockApp:
    def __init__(self):
        self._web_http = MockHttpClient()
        self.llm_configured = None

    def configure_llm(self, provider, api_key, model):
        self.llm_configured = {
            'provider': provider,
            'api_key': api_key,
            'model': model,
        }
        return {'ok': True, 'provider': provider, 'model': model}

mock_app = MockApp()
print(f"应用前 HTTP 超时: {mock_app._web_http.timeout}s")

# 应用配置
mgr.apply_to_app(mock_app)

print(f"应用后 HTTP 超时: {mock_app._web_http.timeout}s")
assert mock_app._web_http.timeout == 45, f"HTTP 超时未生效！期望 45，实际 {mock_app._web_http.timeout}"
print("✓ HTTP 超时配置已生效")

if mock_app.llm_configured:
    print(f"✓ LLM 配置已应用: {mock_app.llm_configured['provider']}/{mock_app.llm_configured['model']}")
    # 验证 API Key 未脱敏（内部使用）
    assert mock_app.llm_configured['api_key'] == "sk-test-1234567890abcdef", "LLM API Key 被脱敏了！"
    print("✓ LLM API Key 内部未脱敏（正确）")
else:
    print("✗ LLM 配置未应用")

# ── 测试 3：导入错误格式的配置 ──
print("\n【测试 3】导入错误格式的配置")
print("-" * 40)

# 错误格式的 URL
invalid_config_json = json.dumps({
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
})

# 模拟前端收集的数据
invalid_config = json.loads(invalid_config_json)

# 前端验证（模拟 JavaScript 的 validateNetworkConfig）
def validate_network_config(config):
    """模拟前端的输入验证逻辑"""
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
print(f"输入的配置包含:")
print(f"  - 代理 URL: {invalid_config['network']['proxy_url']}")
print(f"  - Webhook URL: {invalid_config['external_services']['error_reporting']['webhook_url']}")

if errors:
    print(f"\n✓ 前端验证捕获到 {len(errors)} 个错误:")
    for err in errors:
        print(f"  - {err}")
else:
    print("\n✗ 前端验证未捕获到错误！")

# ── 测试 4：测试后端导入（后端不验证 URL 格式） ──
print("\n【测试 4】后端导入配置（后端不验证 URL 格式）")
print("-" * 40)

try:
    result = mgr.import_config(invalid_config_json)
    print(f"✗ 后端导入了无效 URL（这是预期行为，前端负责验证）")
except ValueError as e:
    print(f"✓ 后端拒绝了无效配置: {e}")
except Exception as e:
    print(f"? 其他错误: {e}")

# ── 测试 5：测试导出/导入循环 ──
print("\n【测试 5】导出/导入配置循环")
print("-" * 40)

exported = mgr.export_config()
exported_config = json.loads(exported)
print(f"✓ 导出的配置包含 {len(exported_config)} 个模块")

# 重置配置
mgr.reset()
print("✓ 配置已重置")

# 导入导出
mgr.import_config(exported)
imported = mgr.get_all()
print(f"✓ 导入后 LLM 超时: {imported['llm']['timeout']}s")
print(f"✓ 导入后代理状态: {imported['network']['proxy_enabled']}")

print("\n" + "=" * 70)
print("测试完成！请查看服务器日志中的 [网络配置] 标记")
print("=" * 70)
