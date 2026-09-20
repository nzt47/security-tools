"""TASK-07 负例 1：SSRF 防护（E1 / E3 / E10）

验收覆盖（TASK-07 §5 E1 逐条）：
    `169.254.169.254` / `127.0.0.1` / `10.x` / `192.168.x` /
    **十进制 IP** / **十六进制 IP** / **302 跳内网** / **DNS rebinding** —— 全部被拒。

外加两条同样硬的门禁：
    · E3 fail-closed：守卫组件失效时出站**被拒绝**（而非放行）；
    · E10 零误伤：正常公网目标**不被拦**（必须有正向测试，避免"全拦了"的假安全）。

【本文件的两个纪律】
  1. **不写生产审计库**：`_isolated_audit` 夹具把进程级审计门面重绑到 `tmp_path`
     （本仓已四次踩到"跑单测写生产数据"，TASK-06 的审计隔离事故是第五次）。
  2. **不打真实网络**：所有出站判定要么是纯计算，要么把 `socket.getaddrinfo`
     与 `socket.create_connection` 都换掉。唯一例外是 E10 的正向用例
     ——它**允许**真实解析（解析失败也判放行，不会因离线而假红）。
"""

from __future__ import annotations

import socket

import pytest

from agent.guardrails import ssrf_guard as G


# ════════════════════════════════════════════════════════════
#  夹具：审计隔离（绝不写 data/audit/audit_chain.db）
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path):
    """把进程级审计门面重绑到临时库（逐用例隔离 + 还原）"""
    from agent.audit import facade as fac
    chain = fac.get_audit_chain(
        str(tmp_path / "audit_chain.db"),
        roots_path=str(tmp_path / "daily_roots.jsonl"),
        signing_key_path=str(tmp_path / "key.pem"))
    previous = fac.audit.bind(chain)
    try:
        yield chain
    finally:
        fac.audit.bind(previous)


@pytest.fixture(autouse=True)
def _guard_defaults(monkeypatch):
    """逐用例把守卫环境变量复位到默认值（防外部环境干扰判定）"""
    monkeypatch.delenv("CP_SSRF_GUARD", raising=False)
    monkeypatch.delenv("CP_SSRF_STRICT_DNS", raising=False)
    monkeypatch.delenv("CP_SSRF_ALLOW_HOSTS", raising=False)


# ════════════════════════════════════════════════════════════
#  一、非标准 IP 写法归一（E1 的"十进制 / 十六进制"两条）
# ════════════════════════════════════════════════════════════


class TestIpLiteralNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.1.", "127.0.0.1"),          # 尾点（FQDN 形态）
        ("2130706433", "127.0.0.1"),          # **十进制**
        ("0x7f000001", "127.0.0.1"),          # **十六进制**
        ("0177.0.0.1", "127.0.0.1"),          # 八进制段
        ("0x7f.0.0.1", "127.0.0.1"),          # 十六进制段
        ("127.1", "127.0.0.1"),               # 少段（inet_aton 语义）
        ("169.254.169.254", "169.254.169.254"),
        ("2852039166", "169.254.169.254"),    # 元数据地址的十进制形态
        ("0xA9FEA9FE", "169.254.169.254"),    # 元数据地址的十六进制形态
        ("::1", "::1"),
        ("[::1]", "::1"),
        ("::ffff:127.0.0.1", "::ffff:7f00:1"),
    ])
    def test_nonstandard_forms_are_normalized(self, raw, expected):
        parsed = G.parse_ip_literal(raw)
        assert parsed is not None, f"{raw!r} 应被识别为 IP 字面量"
        assert str(parsed) == expected

    @pytest.mark.parametrize("raw", ["example.com", "localhost", "foo.local", "", "x" * 300])
    def test_non_literals_are_not_ips(self, raw):
        assert G.parse_ip_literal(raw) is None


# ════════════════════════════════════════════════════════════
#  二、E1：八类地址/手法**全部被拒**
# ════════════════════════════════════════════════════════════


class TestE1SsrfNegativeCases:
    def test_cloud_metadata_link_local(self):
        v = G.check_host("169.254.169.254")
        assert v.allowed is False and v.category == "ip_literal"
        assert "链路本地" in v.note

    def test_loopback(self):
        v = G.check_host("127.0.0.1")
        assert v.allowed is False and "环回" in v.note

    def test_private_10(self):
        v = G.check_host("10.1.2.3")
        assert v.allowed is False and "私有网段 A 类" in v.note

    def test_private_192_168(self):
        v = G.check_host("192.168.1.1")
        assert v.allowed is False and "私有网段 C 类" in v.note

    def test_private_172_17_to_31(self):
        """TASK-07 §2.1 点名：原浏览器黑名单只写了 `172.16.`，漏了 172.17–172.31"""
        for host in ("172.16.0.1", "172.20.5.5", "172.31.255.254"):
            assert G.check_host(host).allowed is False, host
        # 边界外仍是公网（不能把整段 172/8 都拦掉）
        assert G.check_host("172.32.0.1").allowed is True

    def test_decimal_ip(self):
        v = G.check_host("2130706433")
        assert v.allowed is False and v.category == "ip_literal"

    def test_hex_ip(self):
        v = G.check_host("0x7f000001")
        assert v.allowed is False and v.category == "ip_literal"

    def test_cgnat_and_reserved(self):
        for host in ("100.64.0.1", "0.0.0.0", "198.18.0.1", "240.0.0.1"):
            assert G.check_host(host).allowed is False, host

    def test_ipv6_forms(self):
        for host in ("::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "2002:7f00:1::"):
            assert G.check_host(host).allowed is False, host

    def test_url_level_rejects_metadata_endpoint(self):
        """TASK-07 §2.1 的可利用场景原样复现"""
        v = G.check_url("http://169.254.169.254/latest/meta-data/iam/security-credentials/")
        assert v.allowed is False
        assert "169.254.169.254" in v.reason

    def test_url_level_rejects_local_service(self):
        assert G.check_url("http://127.0.0.1:8123/api/state").allowed is False

    def test_dns_name_resolving_to_internal_is_rejected(self, monkeypatch):
        """主机名解析到内网 ⇒ 拒（这就是"DNS 后校验"，`egress.py:95` 自认没做）"""
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM,
                                              socket.IPPROTO_TCP, "", ("10.0.0.7", 80))])
        v = G.check_url("http://intranet.example.com/")
        assert v.allowed is False and v.category == "dns_resolved"
        assert v.resolved_ips == ("10.0.0.7",)

    def test_any_bad_record_among_many_rejects(self, monkeypatch):
        """多 A 记录里**只要有一条**是内网 ⇒ 拒（不能"取第一条看看"）"""
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda *a, **k: [
                                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                 "", ("93.184.216.34", 80)),
                                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                 "", ("127.0.0.1", 80)),
                            ])
        v = G.check_url("http://mixed.example.com/")
        assert v.allowed is False
        assert "127.0.0.1" in v.reason

    def test_non_http_scheme_rejected(self):
        for url in ("file:///etc/passwd", "gopher://x/1", "dict://x:1/a", "ftp://x/a"):
            assert G.check_url(url).allowed is False, url


# ════════════════════════════════════════════════════════════
#  三、302 跳到内网：**每一跳都复检**
# ════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, status_code, headers=None, content=b"ok", url=""):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.encoding = "utf-8"
        self.reason = "OK"
        self.url = url
        self.ok = 200 <= status_code < 400
        self.history = []

        class _Cookies(dict):
            pass

        self.cookies = _Cookies()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


class _FakeSession:
    """记录被请求过的 URL；按脚本返回响应（不打网络）"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requested = []
        self.headers = {}
        self.cookies = {}
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.requested.append(url)
        if not self.responses:
            raise AssertionError(f"未预期的额外请求: {url}")
        resp = self.responses.pop(0)
        resp.url = url
        return resp

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def close(self):
        pass


class TestRedirectRecheck:
    def _client(self, monkeypatch, responses):
        from agent.web.http_client import HttpClient
        client = HttpClient()
        fake = _FakeSession(responses)
        monkeypatch.setattr(client, "_session", fake)
        return client, fake

    def test_redirect_to_metadata_is_blocked(self, monkeypatch):
        """公网 URL → 302 到 `169.254.169.254`：**必须在第二跳前拦下**"""
        client, fake = self._client(monkeypatch, [
            _FakeResp(302, {"location": "http://169.254.169.254/latest/meta-data/"}),
        ])
        result = client.get("https://public.example.com/start")
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert result.get("redirect_blocked_hop") == 1
        # 关键断言：**内网地址从未被真正请求**
        assert fake.requested == ["https://public.example.com/start"]

    def test_redirect_to_private_is_blocked(self, monkeypatch):
        client, fake = self._client(monkeypatch, [
            _FakeResp(301, {"location": "http://10.0.0.1/admin"}),
        ])
        result = client.get("https://public.example.com/")
        assert result.get("blocked") is True
        assert len(fake.requested) == 1

    def test_redirect_chain_relative_location(self, monkeypatch):
        """相对 Location 也要复检（`urljoin` 后仍是公网 ⇒ 放行并记入 history）"""
        client, fake = self._client(monkeypatch, [
            _FakeResp(302, {"location": "/next"}),
            _FakeResp(200, {}),
        ])
        result = client.get("https://public.example.com/start")
        assert result["ok"] is True
        assert fake.requested == ["https://public.example.com/start",
                                  "https://public.example.com/next"]
        assert result["redirect_history"] == ["https://public.example.com/start"]

    def test_allow_redirects_false_does_not_follow(self, monkeypatch):
        client, fake = self._client(monkeypatch, [_FakeResp(302, {"location": "/x"})])
        result = client.get("https://public.example.com/", allow_redirects=False)
        assert len(fake.requested) == 1
        assert result["status_code"] == 302

    def test_requests_module_never_follows_itself(self, monkeypatch):
        """`allow_redirects=False` 必须真的传下去（否则上面的复检全是装饰）"""
        from agent.web.http_client import HttpClient
        client = HttpClient()
        seen = {}

        class _S(_FakeSession):
            def request(self, method, url, **kwargs):
                seen.update(kwargs)
                return super().request(method, url, **kwargs)

        monkeypatch.setattr(client, "_session", _S([_FakeResp(200, {})]))
        client.get("https://public.example.com/")
        assert seen.get("allow_redirects") is False


# ════════════════════════════════════════════════════════════
#  四、DNS rebinding：连接期第二道防线
# ════════════════════════════════════════════════════════════


class _SocketSpy:
    """记录 `socket.socket()` 的创建（**urllib3 的建连原语**）

    【为什么 spy `socket.socket` 而不是 `socket.create_connection`】
    实测 urllib3 v2 的 `util.connection.create_connection()` 是自己
    `socket.socket(af, socktype, proto)` + `sock.connect(sa)`，**不调用**
    `socket.create_connection` —— 打错桩会让"底层 socket 从未创建"这条断言
    永远为真（假绿）。故桩打在真正被调用的那一层。
    """

    def __init__(self):
        self.created = []
        self.connected = []

    def __call__(self, *args, **kwargs):
        self.created.append(args)
        spy = self

        class _FakeSocket:
            def setsockopt(self, *a, **k):
                return None

            def connect(self, address):
                spy.connected.append(address)

            def close(self):
                return None

            def setblocking(self, *a, **k):
                return None

            def settimeout(self, *a, **k):
                return None

        return _FakeSocket()


class TestDnsRebinding:
    def test_connect_time_guard_blocks_rebound_address(self, monkeypatch):
        """第一次解析给公网（过预检），建连时解析变成 127.0.0.1 —— 必须被拦

        被测入口是 `urllib3.util.connection.create_connection`：
        **它正是 urllib3 在建 TCP 连接前解析地址的那一层**
        （真实 requests → urllib3 → 该函数 → socket.socket + connect）。
        """
        G.install_connect_guard()
        try:
            # 预检：解析成公网 ⇒ 放行
            monkeypatch.setattr(socket, "getaddrinfo",
                                lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM,
                                                  socket.IPPROTO_TCP, "",
                                                  ("93.184.216.34", 80))])
            assert G.check_host("rebind.example.com").allowed is True

            # 建连：解析变成环回 ⇒ 必须在 socket 之前被拦
            monkeypatch.setattr(socket, "getaddrinfo",
                                lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM,
                                                  socket.IPPROTO_TCP, "", ("127.0.0.1", 80))])
            spy = _SocketSpy()
            monkeypatch.setattr(socket, "socket", spy)
            from urllib3.util import connection as u3conn
            with G.guard_scope():
                with pytest.raises(G.SsrfBlocked) as exc:
                    u3conn.create_connection(("rebind.example.com", 80))
            assert "环回" in str(exc.value) or "禁止网段" in str(exc.value)
            assert spy.created == [], "拦截必须发生在 socket 创建之前"
            assert spy.connected == [], "不得发生任何连接"
        finally:
            G.uninstall_connect_guard()

    def test_connect_guard_is_inert_outside_scope(self, monkeypatch):
        """**作用域外零影响**：进程内合法的环回访问（后端自身端口）不得被拦"""
        G.install_connect_guard()
        try:
            monkeypatch.setattr(socket, "getaddrinfo",
                                lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM,
                                                  socket.IPPROTO_TCP, "",
                                                  ("127.0.0.1", 5678))])
            spy = _SocketSpy()
            monkeypatch.setattr(socket, "socket", spy)
            from urllib3.util import connection as u3conn
            u3conn.create_connection(("127.0.0.1", 5678))
            assert spy.created, "作用域外应当照常建连（被拦就是误伤本机服务）"
            assert spy.connected == [("127.0.0.1", 5678)]
        finally:
            G.uninstall_connect_guard()

    def test_connect_guard_allows_public_inside_scope(self, monkeypatch):
        """作用域内、目标是公网 ⇒ 放行（E10：不能"全拦了"）"""
        G.install_connect_guard()
        try:
            monkeypatch.setattr(socket, "getaddrinfo",
                                lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM,
                                                  socket.IPPROTO_TCP, "",
                                                  ("93.184.216.34", 443))])
            spy = _SocketSpy()
            monkeypatch.setattr(socket, "socket", spy)
            from urllib3.util import connection as u3conn
            with G.guard_scope():
                u3conn.create_connection(("example.com", 443))
            assert spy.connected == [("93.184.216.34", 443)]
        finally:
            G.uninstall_connect_guard()

    def test_validate_peer_address_reports_reason(self):
        G.install_connect_guard()
        try:
            with G.guard_scope():
                assert G.validate_peer_address(("169.254.169.254", 80)) is not None
                assert G.validate_peer_address(("93.184.216.34", 443)) is None
            assert G.validate_peer_address(("169.254.169.254", 80)) is None
        finally:
            G.uninstall_connect_guard()


# ════════════════════════════════════════════════════════════
#  五、E3：fail-closed（守卫失效 ⇒ 拒绝，不是放行）
# ════════════════════════════════════════════════════════════


class TestFailClosed:
    def test_guard_internal_error_denies(self, monkeypatch):
        monkeypatch.setattr(G, "parse_ip_literal",
                            lambda host: (_ for _ in ()).throw(RuntimeError("boom")))
        v = G.check_host("example.com")
        assert v.allowed is False and v.category == "guard_error"
        assert "fail-closed" in v.reason

    def test_http_client_denies_when_ssrf_component_missing(self, monkeypatch):
        """`HttpClient._ssrf_block` 在守卫导入失败时必须**拒绝**（改动前是放行）"""
        import builtins
        from agent.web import http_client as hc

        real_import = builtins.__import__

        def _deny_import(name, *args, **kwargs):
            if name == "agent.guardrails" or name.startswith("agent.guardrails."):
                raise ImportError("simulated guard outage")
            return real_import(name, *args, **kwargs)

        client = hc.HttpClient()
        monkeypatch.setattr(builtins, "__import__", _deny_import)
        monkeypatch.setattr(client, "_session", _FakeSession([]))
        result = client.get("https://public.example.com/")
        assert result["ok"] is False
        assert result.get("guard_unavailable") is True
        assert result.get("blocked") is True

    def test_explicit_off_switch_restores_legacy_behaviour(self, monkeypatch):
        """回滚口：`CP_SSRF_GUARD=0` ⇒ 判定放行（出站回到改动前行为）"""
        monkeypatch.setenv("CP_SSRF_GUARD", "0")
        assert G.guard_enabled() is False
        assert G.check_url("http://169.254.169.254/").allowed is True
        assert G.check_url("http://127.0.0.1/").allowed is True


# ════════════════════════════════════════════════════════════
#  六、E10：正常出站不被拦（必须有正向测试）
# ════════════════════════════════════════════════════════════


class TestE10NoOverBlocking:
    @pytest.mark.parametrize("url", [
        "https://example.com/",
        "https://www.baidu.com/s?wd=x",
        "https://api.openai.com/v1/models",
        "https://api.tavily.com/search",
        "http://93.184.216.34/",
        "https://[2001:4860:4860::8888]/",
    ])
    def test_public_targets_allowed(self, url):
        """允许真实解析：离线时 `dns_failed` 分支同样判放行，故不会假红"""
        v = G.check_url(url)
        assert v.allowed is True, f"{url} 被误拦：{v.category} {v.reason}"

    def test_whitelist_escape_hatch(self, monkeypatch):
        """显式白名单可放行内网目标（部署侧的确有必须访问的内网服务时）"""
        assert G.check_url("http://10.0.0.5/").allowed is False
        monkeypatch.setenv("CP_SSRF_ALLOW_HOSTS", "10.0.0.5,*.intranet.example.com")
        assert G.check_url("http://10.0.0.5/").allowed is True
        assert G.check_url("http://svc.intranet.example.com/").allowed is True
        assert G.check_url("http://10.0.0.6/").allowed is False

    def test_http_client_public_target_not_blocked(self, monkeypatch):
        from agent.web.http_client import HttpClient
        client = HttpClient()
        fake = _FakeSession([_FakeResp(200, {}, content=b"hello")])
        monkeypatch.setattr(client, "_session", fake)
        result = client.get("https://example.com/")
        assert result["ok"] is True
        assert result["text"] == "hello"


# ════════════════════════════════════════════════════════════
#  七、E11：拦截事件可查且**不含完整 URL**
# ════════════════════════════════════════════════════════════


class TestAuditRedaction:
    def test_block_writes_audit_without_full_url(self, _isolated_audit):
        secret_url = ("http://169.254.169.254/latest/meta-data/"
                      "?token=sk-SUPERSECRET1234567890")
        v = G.check_url(secret_url)
        assert v.allowed is False
        G.audit_block(v, url=secret_url, surface="unit_test")
        rows = _isolated_audit.entries(action="egress_blocked")
        assert rows, "拦截事件必须落审计"
        import json
        blob = json.dumps([r.payload for r in rows], ensure_ascii=False)
        assert "SUPERSECRET" not in blob, "审计里不得出现 URL 的查询串（可能含凭据）"
        assert "latest/meta-data" not in blob, "审计里不得出现 URL 路径"
        assert "token=" not in blob, "审计里不得出现查询串的键"
        # 【实测发现】审计门面的载荷脱敏器（`sensitive_data_filter`）会把 IP 的
        # 后两段打码成 `169.254.xxx.xxx`。这比"前 16 位"更强，故这里断言的是
        # **网段前缀仍在**（判定证据可查）+ **完整 IP 不再出现**（比预期更严）。
        assert "169.254" in blob, "主机网段前缀应当留下（那是判定的必要证据）"
        assert "169.254.169.254" not in blob, (
            "载荷脱敏器本应把 IP 打码；若这条变了，说明脱敏器被改动，需重新评估 §3 第 5 步")


# ════════════════════════════════════════════════════════════
#  八、trust_env=False（防敌意代理劫持）
# ════════════════════════════════════════════════════════════


class TestProxyHardening:
    def test_session_does_not_trust_env(self):
        from agent.web.http_client import HttpClient
        assert HttpClient()._session.trust_env is False

    def test_explicit_proxy_config_still_works(self):
        """显式配置的代理（`network_config`）不受影响（D2）"""
        from agent.web.http_client import HttpClient
        client = HttpClient({"proxy": "http://proxy.example.com:8080"})
        assert client._session.proxies.get("http") == "http://proxy.example.com:8080"
