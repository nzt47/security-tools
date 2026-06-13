import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, mock_open

from agent.web.http_client import HttpClient


class TestHTTPClientInit:
    """测试 HTTP 客户端初始化"""

    def test_init_default_config(self):
        client = HttpClient()
        assert client._config == {}
        assert client._cookies_file is None
        assert client._stats["total_requests"] == 0

    def test_init_with_custom_config(self):
        config = {
            "timeout": 60,
            "max_retries": 5,
            "pool_size": 30,
            "proxy": "http://proxy.example.com:8080"
        }
        client = HttpClient(config)
        assert client._config["timeout"] == 60
        assert client._config["max_retries"] == 5

    def test_init_with_cookies_file(self, tmp_path):
        cookies_file = tmp_path / "cookies.json"
        cookies_file.write_text('{"test": "value"}')
        client = HttpClient({"cookies_file": str(cookies_file)})
        assert client._cookies_file == str(cookies_file)

    def test_init_logging(self):
        with patch("agent.web.http_client.logger") as mock_logger:
            HttpClient()
            mock_logger.info.assert_called_once_with("HTTP 请求引擎已初始化")


class TestHTTPClientBasicRequests:
    """测试基础 HTTP 请求方法"""

    @patch("agent.web.http_client.requests.Session")
    def test_get_request_success(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.reason = "OK"
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.content = b"Hello World"
        mock_resp.text = "Hello World"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.get("http://example.com")

        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["text"] == "Hello World"

    @patch("agent.web.http_client.requests.Session")
    def test_post_request_with_json(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 201
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.content = b'{"result": "success"}'
        mock_resp.text = '{"result": "success"}'
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com/api"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.post("http://example.com/api", json_data={"key": "value"})

        assert result["ok"] is True
        assert result["status_code"] == 201

    @patch("agent.web.http_client.requests.Session")
    def test_head_request(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "100"}
        mock_resp.content = b""
        mock_resp.text = ""
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.head("http://example.com")

        assert result["ok"] is True
        assert result["headers"]["Content-Length"] == "100"

    def test_invalid_protocol(self):
        client = HttpClient()
        result = client.get("ftp://example.com")
        assert result["ok"] is False
        assert "仅支持 http/https 协议" in result["error"]


class TestHTTPClientErrorHandling:
    """测试异常处理"""

    @patch("agent.web.http_client.requests.Session")
    def test_request_timeout(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        from requests.exceptions import Timeout
        mock_session.request.side_effect = Timeout("Connection timed out")

        client = HttpClient()
        result = client.get("http://example.com", timeout=1)

        assert result["ok"] is False
        assert "请求超时" in result["error"]

    @patch("agent.web.http_client.requests.Session")
    def test_connection_error(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        from requests.exceptions import ConnectionError
        mock_session.request.side_effect = ConnectionError("Connection refused")

        client = HttpClient()
        result = client.get("http://example.com")

        assert result["ok"] is False
        assert "连接失败" in result["error"]

    @patch("agent.web.http_client.requests.Session")
    def test_http_error(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.reason = "Not Found"
        mock_resp.headers = {}
        mock_resp.content = b"Not Found"
        mock_resp.text = "Not Found"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com/notfound"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.get("http://example.com/notfound")

        assert result["ok"] is False
        assert "HTTP 404" in result["error"]

    @patch("agent.web.http_client.requests.Session")
    def test_unknown_exception(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.request.side_effect = Exception("Unknown error")

        client = HttpClient()
        result = client.get("http://example.com")

        assert result["ok"] is False
        assert "未知错误" in result["error"]


class TestHTTPClientCookieManagement:
    """测试 Cookie 管理"""

    def test_set_and_get_cookies(self):
        client = HttpClient()
        client.set_cookies({"session": "abc123"})
        cookies = client.get_cookies()
        assert cookies["session"] == "abc123"

    def test_set_cookies_with_domain(self):
        client = HttpClient()
        client.set_cookies({"session": "abc123"}, domain="example.com")
        cookies = client.get_cookies()
        assert "session" in cookies

    def test_clear_cookies(self):
        client = HttpClient()
        client.set_cookies({"session": "abc123"})
        client.clear_cookies()
        cookies = client.get_cookies()
        assert cookies == {}

    def test_load_cookies_from_file(self, tmp_path):
        cookies_file = tmp_path / "cookies.json"
        cookies_file.write_text('{"session": "loaded_value"}')
        client = HttpClient({"cookies_file": str(cookies_file)})
        cookies = client.get_cookies()
        assert cookies["session"] == "loaded_value"

    def test_load_cookies_file_not_found(self, tmp_path):
        cookies_file = tmp_path / "nonexistent.json"
        client = HttpClient({"cookies_file": str(cookies_file)})
        assert client.get_cookies() == {}

    def test_save_cookies_to_file(self, tmp_path):
        cookies_file = tmp_path / "cookies.json"
        client = HttpClient({"cookies_file": str(cookies_file)})
        client.set_cookies({"session": "save_value"})
        client.save_cookies()
        with open(cookies_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["session"] == "save_value"


class TestHTTPClientProxy:
    """测试代理功能"""

    @patch("agent.web.http_client.requests.Session")
    def test_init_with_proxy(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        HttpClient({"proxy": "http://proxy.example.com:8080"})

        mock_session.mount.assert_any_call("http://", mock_session.mount.call_args_list[0][0][1])
        mock_session.mount.assert_any_call("https://", mock_session.mount.call_args_list[1][0][1])

    def test_set_proxy_dynamically(self):
        client = HttpClient()
        client.set_proxy("http://proxy.example.com:8080")
        assert client._session.proxies == {"http": "http://proxy.example.com:8080", "https": "http://proxy.example.com:8080"}

    def test_clear_proxy(self):
        client = HttpClient({"proxy": "http://proxy.example.com:8080"})
        client.set_proxy(None)
        assert client._session.proxies == {}


class TestHTTPClientDownload:
    """测试文件下载功能"""

    @patch("agent.web.http_client.requests.Session")
    def test_download_success(self, mock_session_class, tmp_path):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_session.get.return_value = mock_resp

        client = HttpClient()
        filepath = str(tmp_path / "test.txt")
        result = client.download("http://example.com/file.txt", filepath)

        assert result["ok"] is True
        assert result["filepath"] == filepath
        assert result["size"] == 12

    @patch("agent.web.http_client.requests.Session")
    def test_download_failure(self, mock_session_class, tmp_path):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = Exception("Download failed")

        client = HttpClient()
        filepath = str(tmp_path / "test.txt")
        result = client.download("http://example.com/file.txt", filepath)

        assert result["ok"] is False
        assert "Download failed" in result["error"]


class TestHTTPClientBatchRequest:
    """测试批量请求功能"""

    @patch("agent.web.http_client.requests.Session")
    def test_batch_request(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        results = client.batch_request(["http://a.com", "http://b.com", "http://c.com"], max_concurrency=2)

        assert len(results) == 3
        assert all(r["ok"] for r in results)


class TestHTTPClientURLTools:
    """测试 URL 工具方法"""

    def test_is_valid_url(self):
        assert HttpClient.is_valid_url("http://example.com") is True
        assert HttpClient.is_valid_url("https://example.com/path") is True
        assert HttpClient.is_valid_url("ftp://example.com") is True
        assert HttpClient.is_valid_url("example.com") is False
        assert HttpClient.is_valid_url("") is False

    def test_join_url(self):
        assert HttpClient.join_url("http://example.com/api/", "users") == "http://example.com/api/users"
        assert HttpClient.join_url("http://example.com/api/", "/users") == "http://example.com/users"
        assert HttpClient.join_url("http://example.com", "http://other.com") == "http://other.com"


class TestHTTPClientSessionManagement:
    """测试会话管理"""

    def test_update_headers(self):
        client = HttpClient()
        client.update_headers({"X-Custom": "value"})
        assert client._session.headers["X-Custom"] == "value"

    def test_reset_session(self):
        client = HttpClient()
        client.set_cookies({"session": "value"})
        client.reset_session()
        # Cookie 应该被保留
        assert "session" in client.get_cookies()

    def test_close_session(self):
        client = HttpClient()
        with patch.object(client, "save_cookies") as mock_save:
            client.close()
            mock_save.assert_called_once()

    def test_context_manager(self):
        with HttpClient() as client:
            assert client._session is not None


class TestHTTPClientStats:
    """测试统计功能"""

    @patch("agent.web.http_client.requests.Session")
    def test_get_stats(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        client.get("http://example.com")
        stats = client.get_stats()

        assert stats["total_requests"] == 1
        assert stats["success_count"] == 1
        assert "uptime_sec" in stats


class TestHTTPClientSSL:
    """测试 SSL 验证"""

    @patch("agent.web.http_client.requests.Session")
    def test_ssl_verify_false(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "https://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.get("https://example.com", verify=False)

        assert result["ok"] is True
        _, kwargs = mock_session.request.call_args
        assert kwargs.get("verify") is False


class TestHTTPClientDownloadAdditional:
    """测试下载功能的额外场景"""

    @patch("agent.web.http_client.requests.Session")
    def test_download_with_chunk_size(self, mock_session_class, tmp_path):
        """测试自定义分块大小"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_session.get.return_value = mock_resp

        client = HttpClient()
        filepath = str(tmp_path / "test.txt")
        result = client.download("http://example.com/file.txt", filepath, chunk_size=1024)

        assert result["ok"] is True
        mock_resp.iter_content.assert_called_with(chunk_size=1024)

    @patch("agent.web.http_client.requests.Session")
    def test_download_create_parent_dirs(self, mock_session_class, tmp_path):
        """测试自动创建父目录"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"content"]
        mock_session.get.return_value = mock_resp

        client = HttpClient()
        filepath = str(tmp_path / "subdir" / "nested" / "file.txt")
        result = client.download("http://example.com/file.txt", filepath)

        assert result["ok"] is True
        assert (tmp_path / "subdir" / "nested" / "file.txt").exists()


class TestHTTPClientBatchRequestAdditional:
    """测试批量请求的额外场景"""

    @patch("agent.web.http_client.requests.Session")
    def test_batch_request_empty_urls(self, mock_session_class):
        """测试空URL列表"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = HttpClient()
        results = client.batch_request([])

        assert results == []

    @patch("agent.web.http_client.requests.Session")
    def test_batch_request_single_url(self, mock_session_class):
        """测试单URL批量请求"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        results = client.batch_request(["http://example.com"])

        assert len(results) == 1
        assert results[0]["ok"] is True


class TestHTTPClientCookieAdditional:
    """测试Cookie管理的额外场景"""

    def test_get_cookies_with_domain(self):
        """测试按域名获取Cookie"""
        client = HttpClient()
        client.set_cookies({"session": "value"}, domain="example.com")
        cookies = client.get_cookies(domain="example.com")
        assert "session" in cookies

    def test_load_cookies_invalid_json(self, tmp_path):
        """测试加载无效JSON格式的Cookie文件"""
        cookies_file = tmp_path / "invalid.json"
        cookies_file.write_text("{invalid json}")
        with patch("agent.web.http_client.logger") as mock_logger:
            client = HttpClient({"cookies_file": str(cookies_file)})
            mock_logger.warning.assert_called_once()

    def test_save_cookies_no_file_configured(self):
        """测试未配置cookies_file时保存Cookie"""
        client = HttpClient()
        client.set_cookies({"session": "value"})
        # 不应抛出异常
        client.save_cookies()


class TestHTTPClientRequestAdditional:
    """测试请求方法的额外场景"""

    @patch("agent.web.http_client.requests.Session")
    def test_request_stream_mode(self, mock_session_class):
        """测试流式响应模式"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/octet-stream"}
        mock_resp.content = b"streamed content"
        mock_resp.text = "streamed content"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com/stream"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.request("GET", "http://example.com/stream", stream=True)

        assert result["ok"] is True
        assert result["content"] is None  # 流式模式不自动读取
        assert result["text"] is None

    @patch("agent.web.http_client.requests.Session")
    def test_request_with_params(self, mock_session_class):
        """测试URL查询参数"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com?key=value"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.get("http://example.com", params={"key": "value"})

        assert result["ok"] is True
        _, kwargs = mock_session.request.call_args
        assert kwargs.get("params") == {"key": "value"}

    @patch("agent.web.http_client.requests.Session")
    def test_request_with_form_data(self, mock_session_class):
        """测试表单数据"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com/submit"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.post("http://example.com/submit", data={"username": "test"})

        assert result["ok"] is True
        _, kwargs = mock_session.request.call_args
        assert kwargs.get("data") == {"username": "test"}

    @patch("agent.web.http_client.requests.Session")
    def test_request_redirect_history(self, mock_session_class):
        """测试重定向历史"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"OK"
        mock_resp.text = "OK"
        mock_resp.encoding = "utf-8"
        mock_resp.url = "http://example.com/final"
        mock_resp.cookies = {}
        
        mock_history_resp1 = MagicMock()
        mock_history_resp1.url = "http://example.com/redirect1"
        mock_history_resp2 = MagicMock()
        mock_history_resp2.url = "http://example.com/redirect2"
        mock_resp.history = [mock_history_resp1, mock_history_resp2]
        
        mock_session.request.return_value = mock_resp

        client = HttpClient()
        result = client.get("http://example.com/start")

        assert result["ok"] is True
        assert result["redirect_history"] == ["http://example.com/redirect1", "http://example.com/redirect2"]


class TestHTTPClientEncodingDetection:
    """测试编码自动检测"""

    @patch("agent.web.http_client.requests.Session")
    def test_request_chardet_detection(self, mock_session_class, monkeypatch):
        """测试chardet编码检测"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.content = b"\xe4\xb8\xad\xe6\x96\x87"  # UTF-8中文
        mock_resp.text = ""
        mock_resp.encoding = None  # 没有设置编码
        mock_resp.url = "http://example.com"
        mock_resp.cookies = {}
        mock_resp.history = []
        mock_session.request.return_value = mock_resp
        
        # 模拟chardet模块
        mock_chardet_module = MagicMock()
        mock_chardet_module.detect.return_value = {"encoding": "utf-8"}
        monkeypatch.setitem(sys.modules, "chardet", mock_chardet_module)

        client = HttpClient()
        result = client.get("http://example.com")

        assert result["ok"] is True
        assert result["encoding"] == "utf-8"


class TestHTTPClientSessionReset:
    """测试会话重置"""

    def test_reset_session_preserves_cookies(self):
        """测试重置会话保留Cookie"""
        client = HttpClient()
        client.set_cookies({"session": "value1", "token": "value2"})
        old_session_id = id(client._session)
        
        client.reset_session()
        
        new_session_id = id(client._session)
        assert old_session_id != new_session_id  # 会话已更新
        cookies = client.get_cookies()
        assert cookies["session"] == "value1"
        assert cookies["token"] == "value2"


class TestHTTPClientContextManager:
    """测试上下文管理器"""

    def test_context_manager_close(self):
        """测试上下文管理器自动关闭会话"""
        with patch("agent.web.http_client.HttpClient.close") as mock_close:
            with HttpClient():
                pass
            mock_close.assert_called_once()
