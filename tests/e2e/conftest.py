"""E2E 测试夹具——支持两种模式：
1. 已有服务器在运行：直接连接（推荐）
2. 无服务器：自动启动+清理
"""
import os
import subprocess
import time
import sys
from pathlib import Path
import pytest
import requests

SERVER_URL = "http://127.0.0.1:5678"
SERVER_SCRIPT = str(Path(__file__).parents[2] / "app_server.py")


def _server_alive() -> bool:
    """检查服务器是否已在运行"""
    try:
        resp = requests.get(f"{SERVER_URL}/api/health", timeout=2)
        return resp.status_code == 200
    except requests.ConnectionError:
        return False


@pytest.fixture(scope="session")
def server():
    """启动测试服务器（会话级），如已有则直接连接"""
    proc = None

    if not _server_alive():
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
        env.update({
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": "test-key",
            "YUNSHU_FEATURE_SANDBOX": "false",
        })

        proc = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )

        # 等待服务器就绪（最多 120 秒）
        last_error = None
        for i in range(120):
            try:
                resp = requests.get(f"{SERVER_URL}/api/health", timeout=3)
                if resp.status_code == 200:
                    break
            except requests.ConnectionError as e:
                last_error = e
                time.sleep(1)
            except Exception as e:
                last_error = e
                time.sleep(1)
        else:
            proc.terminate()
            proc.wait(timeout=5)
            raise RuntimeError(f"服务器启动超时 (120s)，最后错误: {last_error}")
    else:
        print("[conftest] 使用已在运行的服务器", flush=True)

    yield SERVER_URL

    # 仅清理自己启动的服务器
    if proc:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def client(server):
    """每个测试的 HTTP 客户端"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session
