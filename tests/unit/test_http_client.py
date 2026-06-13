"""
HTTP 客户端测试
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from agent.web.http_client import (
    HttpClient,
    DEFAULT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_POOL_SIZE,
)


class TestHttpClientInit:
    """测试 HTTP 客户端初始化"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_default_config(self):
        """测试默认配置初始化"""
        client = HttpClient()
        
        assert client._config == {}
        assert client._session is not None
        assert client._stats["total_requests"] == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_custom_config(self):
        """测试自定义配置初始化"""
        config = {
            "max_retries": 5,
            "timeout": 60,
            "proxy": "http://proxy.example.com:8080",
        }
        client = HttpClient(config=config)
        
        assert client._config["max_retries"] == 5
        assert client._config["timeout"] == 60

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_cookies_file(self):
        """测试带 Cookie 文件初始化"""
        config = {"cookies_file": "/tmp/cookies.json"}
        client = HttpClient(config=config)
        
        assert client._cookies_file == "/tmp/cookies.json"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_session_headers(self):
        """测试 Session 默认请求头"""
        client = HttpClient()
        
        headers = client._session.headers
        assert "Accept" in headers
        assert "Accept-Language" in headers
        assert "Connection" in headers

    @pytest.mark.unit
    @pytest.mark.p1
    def test_session_adapters(self):
        """测试 Session 适配器"""
        client = HttpClient()
        
        # 应该有 HTTP 和 HTTPS 适配器
        assert "http://" in client._session.adapters
        assert "https://" in client._session.adapters


class TestHttpClientDefaults:
    """测试默认常量"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_timeout(self):
        """测试默认超时"""
        assert DEFAULT_TIMEOUT == 30

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_max_retries(self):
        """测试默认重试次数"""
        assert DEFAULT_MAX_RETRIES == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_pool_size(self):
        """测试默认连接池大小"""
        assert DEFAULT_POOL_SIZE == 20


class TestHttpClientStats:
    """测试统计功能"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_initial_stats(self):
        """测试初始统计"""
        client = HttpClient()
        
        assert client._stats["total_requests"] == 0
        assert client._stats["success_count"] == 0
        assert client._stats["error_count"] == 0
        assert client._stats["total_bytes"] == 0
        assert "started_at" in client._stats

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_stats(self):
        """测试获取统计"""
        client = HttpClient()
        
        stats = client.get_stats()
        assert stats["total_requests"] == 0


class TestHttpClientMethods:
    """测试 HTTP 方法"""

    @pytest.mark.unit
    @pytest.mark.p1
    @patch("requests.Session.request")
    def test_get_request(self, mock_request):
        """测试 GET 请求"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "Hello World"
        mock_response.headers = {"Content-Type": "text/html"}  # 真正的字典
        mock_response.content = b"Hello World"
        mock_response.cookies = {}
        mock_response.url = "http://example.com"
        mock_response.history = []
        mock_request.return_value = mock_response
        
        client = HttpClient()
        result = client.get("http://example.com")
        
        mock_request.assert_called()
        assert result["ok"]

    @pytest.mark.unit
    @pytest.mark.p1
    @patch("requests.Session.request")
    def test_post_request(self, mock_request):
        """测试 POST 请求"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_response.text = '{"status": "ok"}'
        mock_response.headers = {"Content-Type": "application/json"}  # 真正的字典
        mock_response.content = b'{"status": "ok"}'
        mock_response.cookies = {}
        mock_response.url = "http://example.com/api"
        mock_response.history = []
        mock_request.return_value = mock_response
        
        client = HttpClient()
        result = client.post("http://example.com/api", data={"key": "value"})
        
        mock_request.assert_called()
        assert result["ok"]

    @pytest.mark.unit
    @pytest.mark.p1
    @patch("requests.Session.request")
    def test_request_error(self, mock_request):
        """测试请求错误"""
        mock_request.side_effect = requests.RequestException("Network error")
        
        client = HttpClient()
        result = client.get("http://example.com")
        
        assert not result["ok"]
        assert "error" in result


class TestHttpClientSession:
    """测试会话管理"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_close_session(self):
        """测试关闭会话"""
        client = HttpClient()
        client.close()
        
        # 会话应该被关闭

    @pytest.mark.unit
    @pytest.mark.p1
    def test_session_persistence(self):
        """测试会话持久性"""
        client = HttpClient()
        
        # 同一个会话应该被复用
        session1 = client._session
        assert session1 is client._session