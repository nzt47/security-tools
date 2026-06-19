"""HTTP Client 单元测试"""
import pytest
from unittest.mock import patch, MagicMock
import requests

from agent.web.http_client import HttpClient


class TestHttpClient:
    """测试 HTTP 客户端"""

    def test_http_client_init(self):
        """测试初始化"""
        client = HttpClient()
        
        assert client._session is not None
        assert client._stats is not None

    @patch("requests.Session")
    def test_http_client_get(self, mock_session_class):
        """测试 GET 请求"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.content = b"<html></html>"
        mock_response.text = "<html></html>"
        mock_response.cookies = {}
        mock_response.history = []
        mock_response.url = "https://example.com"
        mock_session.request.return_value = mock_response
        
        client = HttpClient()
        result = client.get("https://example.com")
        
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["url"] == "https://example.com"

    @patch("requests.Session")
    def test_http_client_post(self, mock_session_class):
        """测试 POST 请求"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 201
        mock_response.reason = "Created"
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.content = b'{"id": 1}'
        mock_response.text = '{"id": 1}'
        mock_response.cookies = {}
        mock_response.history = []
        mock_response.url = "https://example.com/api"
        mock_session.request.return_value = mock_response
        
        client = HttpClient()
        result = client.post(
            "https://example.com/api",
            json_data={"name": "test"}
        )
        
        assert result["status_code"] == 201

    @patch("requests.Session")
    def test_http_client_post_form_data(self, mock_session_class):
        """测试 POST 表单数据"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.text = "OK"
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.cookies = {}
        mock_response.history = []
        mock_response.url = "https://example.com/submit"
        mock_session.request.return_value = mock_response
        
        client = HttpClient()
        result = client.post(
            "https://example.com/submit",
            data={"username": "test", "password": "secret"}
        )
        
        assert result["status_code"] == 200

    @patch("requests.Session")
    def test_http_client_timeout(self, mock_session_class):
        """测试请求超时"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_session.request.side_effect = requests.exceptions.Timeout("Connection timed out")
        
        client = HttpClient()
        result = client.get("https://example.com", timeout=0.1)
        
        assert result["ok"] is False
        assert "超时" in result["error"]

    @patch("requests.Session")
    def test_http_client_http_error(self, mock_session_class):
        """测试 HTTP 错误"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.reason = "Not Found"
        mock_response.headers = {}
        mock_response.content = b"Not Found"
        mock_response.text = "Not Found"
        mock_response.cookies = {}
        mock_response.history = []
        mock_response.url = "https://example.com/not_found"
        mock_session.request.return_value = mock_response
        
        client = HttpClient()
        result = client.get("https://example.com/not_found")
        
        assert result["ok"] is False
        assert result["status_code"] == 404

    @patch("requests.Session")
    def test_http_client_with_proxy(self, mock_session_class):
        """测试代理配置"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.text = "OK"
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.cookies = {}
        mock_response.history = []
        mock_response.url = "https://example.com"
        mock_session.request.return_value = mock_response
        
        client = HttpClient({"proxy": "http://proxy.example.com:8080"})
        result = client.get("https://example.com")
        
        assert result["status_code"] == 200
        assert mock_session.proxies == {"http": "http://proxy.example.com:8080", "https": "http://proxy.example.com:8080"}

    @patch("requests.Session")
    def test_http_client_close(self, mock_session_class):
        """测试关闭客户端"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        client = HttpClient()
        client.close()
        
        mock_session.close.assert_called_once()

    def test_http_client_is_valid_url(self):
        """测试 URL 验证"""
        assert HttpClient.is_valid_url("https://example.com") is True
        assert HttpClient.is_valid_url("http://example.com") is True
        assert HttpClient.is_valid_url("invalid") is False

    def test_http_client_join_url(self):
        """测试 URL 拼接"""
        result = HttpClient.join_url("https://example.com/api/", "users")
        assert result == "https://example.com/api/users"

    @patch("requests.Session")
    def test_http_client_get_stats(self, mock_session_class):
        """测试获取统计信息"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.text = "OK"
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.cookies = {}
        mock_response.history = []
        mock_response.url = "https://example.com"
        mock_session.request.return_value = mock_response
        
        client = HttpClient()
        client.get("https://example.com")
        
        stats = client.get_stats()
        
        assert "total_requests" in stats
        assert "success_count" in stats
        assert stats["total_requests"] == 1