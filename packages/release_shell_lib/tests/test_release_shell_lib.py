"""release_shell_lib 包测试 — 对齐 shell 版 test_lib.sh 验证过的场景。

网络用例全部走本地 HTTP server，不依赖外网（外网访问 github/docker hub 实测很慢）。
"""

import http.server
import os
import socket
import tempfile
import threading
import unittest

from release_shell_lib import (
    curl_http_code,
    gh_api_len,
    read_resp_file,
    safe_num_or_zero,
)


class _Handler(http.server.BaseHTTPRequestHandler):
    """固定行为：/ok -> 200；/missing -> 404；其余 -> 200 返回 body。"""

    def do_GET(self):
        if self.path.startswith("/missing"):
            body = b"not found body"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/body"):
            body = b"hello-release-sim"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):  # 静音请求日志
        pass


class LocalServer:
    """本地 HTTP 测试服务器（每个测试类起一个）。"""

    def __init__(self):
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


class TestSafeNumOrZero(unittest.TestCase):
    """safe_num_or_zero 正则兜底：非纯数字一律 0。"""

    def test_pure_number(self):
        self.assertEqual(safe_num_or_zero("12"), 12)
        self.assertEqual(safe_num_or_zero("0"), 0)

    def test_mixed_alpha(self):
        self.assertEqual(safe_num_or_zero("ab12"), 0)
        self.assertEqual(safe_num_or_zero("12ab"), 0)

    def test_empty_and_none(self):
        self.assertEqual(safe_num_or_zero(""), 0)
        self.assertEqual(safe_num_or_zero(None), 0)

    def test_whitespace(self):
        self.assertEqual(safe_num_or_zero(" 12 "), 12)
        self.assertEqual(safe_num_or_zero("   "), 0)

    def test_json_garbage(self):
        # gh 错误 JSON 混入 stdout 的典型形态
        self.assertEqual(safe_num_or_zero('{"message": "Bad credentials"}0'), 0)

    def test_bool_excluded(self):
        # Python bool 是 int 子类，必须排除
        self.assertEqual(safe_num_or_zero(True), 0)
        self.assertEqual(safe_num_or_zero(False), 0)


class TestCurlHttpCode(unittest.TestCase):
    """curl_http_code：真实 HTTP 请求 + 网络失败映射 500。"""

    def test_success_returns_code_and_writes_file(self):
        with LocalServer() as srv, tempfile.TemporaryDirectory() as d:
            resp_file = os.path.join(d, "resp.json")
            code = curl_http_code(f"{srv.base}/ok", timeout=5, resp_file=resp_file)
            self.assertEqual(code, 200)
            self.assertTrue(os.path.isfile(resp_file))

    def test_http_error_returns_real_code(self):
        with LocalServer() as srv, tempfile.TemporaryDirectory() as d:
            resp_file = os.path.join(d, "resp.json")
            code = curl_http_code(f"{srv.base}/missing", timeout=5, resp_file=resp_file)
            self.assertEqual(code, 404)
            self.assertTrue(os.path.isfile(resp_file))  # 错误响应体也要落盘

    def test_body_written_to_file(self):
        with LocalServer() as srv, tempfile.TemporaryDirectory() as d:
            resp_file = os.path.join(d, "resp.json")
            curl_http_code(f"{srv.base}/body", timeout=5, resp_file=resp_file)
            with open(resp_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "hello-release-sim")

    def test_connection_refused_maps_500(self):
        # 拿一个未监听端口（绑定后关闭再连）→ 连接拒绝 → 500
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        code = curl_http_code(f"http://127.0.0.1:{port}", timeout=5)
        self.assertEqual(code, 500)

    def test_dns_failure_maps_500(self):
        code = curl_http_code("http://nonexistent.invalid", timeout=5)
        self.assertEqual(code, 500)


class TestGhApiLen(unittest.TestCase):
    """gh_api_len：gh CLI 缺失/失败/非数字输出一律 0。"""

    def test_gh_missing_returns_int_zero(self):
        # 环境无 gh 时 subprocess 抛 FileNotFoundError → 0
        code = gh_api_len("repo/x/check-runs", "[] | length", timeout=5)
        self.assertEqual(code, 0)
        self.assertIsInstance(code, int)


class TestReadRespFile(unittest.TestCase):
    """read_resp_file：缺失/空文件给提示，正常文件返回截断内容。"""

    def test_missing_file_returns_hint(self):
        self.assertEqual(
            read_resp_file("/nonexistent/path/x.json"),
            "(无——网络层失败，已按 HTTP 500 进入重试)",
        )

    def test_empty_file_returns_hint(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "empty.json")
            with open(f, "w"):
                pass
            self.assertEqual(
                read_resp_file(f),
                "(无——网络层失败，已按 HTTP 500 进入重试)",
            )

    def test_nonempty_file_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "resp.json")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("x" * 1000)
            self.assertEqual(len(read_resp_file(f, max_chars=300)), 300)


if __name__ == "__main__":
    unittest.main()
