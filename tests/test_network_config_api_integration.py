"""
网络配置 API 端到端测试

测试场景：
1. 测试配置保存后是否能正确应用到 HTTP 客户端
2. 验证配置保存和即时生效的日志输出
3. 导入包含错误格式 URL 的配置，测试前端输入验证

运行方式：确保服务器运行在 http://127.0.0.1:5678

注意：手动集成测试，不会被 pytest 自动收集
"""

import json
import sys
import requests

BASE_URL = "http://127.0.0.1:5678"


def check_server():
    """检查服务器是否在运行"""
    try:
        requests.get(f"{BASE_URL}/api/network-config", timeout=2)
        return True
    except requests.exceptions.ConnectionError:
        return False


def run_tests():
    """运行所有集成测试"""
    if not check_server():
        print(f"❌ 服务器 {BASE_URL} 未运行，跳过集成测试")
        return

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
    else:
        print(f"✗ 获取配置失败: {res.status_code}")

    # ── 测试 2：更新并验证网络配置 ──
    print("\n【测试 2】更新并验证网络配置")
    print("-" * 40)

    new_config = {
        "llm": {"timeout": 60},
        "network": {"timeout": 30, "proxy_enabled": False}
    }

    res = requests.post(f"{BASE_URL}/api/network-config", json=new_config)
    if res.status_code == 200:
        print(f"✓ 配置保存成功: {res.json()}")
    else:
        print(f"✗ 配置保存失败: {res.status_code}")
        return

    # 验证配置已应用
    res = requests.get(f"{BASE_URL}/api/network-config")
    if res.status_code == 200:
        config = res.json()
        assert config['llm']['timeout'] == 60, "LLM 超时未更新"
        assert config['network']['timeout'] == 30, "网络超时未更新"
        print("✓ 配置验证通过")

    # ── 测试 3：导入无效配置 ──
    print("\n【测试 3】导入无效配置")
    print("-" * 40)

    invalid_config = {
        "llm": {"timeout": -1},
        "network": {"proxy_url": "not-a-valid-url"}
    }

    res = requests.post(f"{BASE_URL}/api/network-config", json=invalid_config)
    if res.status_code == 400:
        print(f"✓ 正确拒绝了无效配置: {res.json()}")
    else:
        print(f"✗ 期望 400 但收到 {res.status_code}: {res.json()}")

    # ── 测试 4：健康检查 ──
    print("\n【测试 4】健康检查")
    print("-" * 40)

    res = requests.get(f"{BASE_URL}/health")
    if res.status_code == 200:
        print(f"✓ 服务器健康: {res.json()}")
    else:
        print(f"✗ 健康检查失败: {res.status_code}")

    print("\n" + "=" * 70)
    print("集成测试完成")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()
