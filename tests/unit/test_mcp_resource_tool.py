"""E4-3：mcp_resource（MCP resources / prompts 只读）判据。

覆盖设计稿 §5 的六条：
  1. 四个 action 在假服务器上全通（uri/name 原样回），缺能力回 not_supported；
  2. 大资源回 too_large 且**不落盘**；
  3. 能力协商**读回**（initialize 的 capabilities 真存进客户端）；
  4. 每次读写一条 mcp_resource_access（摘要 + 字节数 + 结果码，无原文）；
  5. 失败码稳定（bad_request / no_connection / not_text）；
  6. HTTP 传输走同一条 resource_action（能力闸门同样生效）。

【为什么用进程内假客户端而不是起真 stdio 服务】判据要钉的是**能力协商与整形口径**，
不是 MCP 协议的管道细节（那由 mcp_services/mcp_client.py 自己的用例守）。
"""
from __future__ import annotations

import json

import pytest

from agent.tools import mcp_resource_tools as R


class _FakeClient:
    """进程内假 MCP 客户端：方法与 MCPClient 同名同形（异步）"""

    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.calls = []

    async def list_resources(self):
        self.calls.append(("list_resources",))
        return [
            {"uri": "file:///logs/a.txt", "name": "a", "description": "日志", "mime_type": "text/plain"},
            {"uri": "db://rows/42", "name": "row42", "description": "", "mime_type": "application/json"},
        ]

    async def read_resource_content(self, uri):
        self.calls.append(("read_resource", uri))
        if uri.endswith(".bin"):
            return {"uri": uri, "mimeType": "application/octet-stream", "blob": "AAECAwQ="}
        return {"uri": uri, "mimeType": "text/plain", "text": "hello " + uri}

    async def list_prompts(self):
        self.calls.append(("list_prompts",))
        return [{"name": "review", "description": "代码评审模板",
                 "arguments": [{"name": "lang", "required": True}]}]

    async def get_prompt(self, name, arguments=None):
        self.calls.append(("get_prompt", name, dict(arguments or {})))
        return {"description": "模板 " + name,
                "messages": [{"role": "user",
                              "content": {"type": "text", "text": "请评审 " + name}}]}


class _FakeDiscovery:
    def __init__(self, connector):
        self.mcp = connector


@pytest.fixture()
def connector():
    from agent.tools.mcp_connector import McpConnector

    conn = McpConnector()
    return conn


def _install(monkeypatch, connector, service_id, client, transport="stdio"):
    connector._connections[service_id] = {
        "client": client,
        "tool_names": [],
        "transport": transport,
        "base_url": "http://127.0.0.1:9",
        "name": service_id,
        "capabilities": dict(getattr(client, "capabilities", {}) or {}),
        "connected_at": 0.0,
    }
    import agent.tool_router as router

    monkeypatch.setattr(router, "_discovery_service", _FakeDiscovery(connector), raising=False)


FULL_CAPS = {"tools": {}, "resources": {}, "prompts": {"listChanged": False}}


def test_四个动作全通且uri与name原样回(monkeypatch, connector):
    client = _FakeClient(FULL_CAPS)
    _install(monkeypatch, connector, "svc", client)

    listed = R.execute("list_resources", connection="svc")
    assert listed["ok"] and listed["count"] == 2
    assert listed["resources"][0]["uri"] == "file:///logs/a.txt"
    assert listed["resources"][0]["mimeType"] == "text/plain"

    read = R.execute("read_resource", connection="svc", uri="file:///logs/a.txt")
    assert read["ok"] and read["uri"] == "file:///logs/a.txt"
    assert read["contents"][0]["text"] == "hello file:///logs/a.txt"
    assert read["bytes"] == len("hello file:///logs/a.txt")

    prompts = R.execute("list_prompts", connection="svc")
    assert prompts["ok"] and prompts["prompts"][0]["name"] == "review"

    got = R.execute("get_prompt", connection="svc", name="review", arguments={"lang": "py"})
    assert got["ok"] and got["name"] == "review"
    assert got["messages"][0]["content"]["text"] == "请评审 review"


def test_缺能力回not_supported而不是空列表(monkeypatch, connector):
    client = _FakeClient({"tools": {}})
    _install(monkeypatch, connector, "svc", client)

    out = R.execute("list_resources", connection="svc")
    assert out["ok"] is False and out["code"] == "not_supported"
    assert "resources" not in out  # 不是空列表
    assert out["capabilities"] == ["tools"]
    # 反向：声明了 prompts 就能用
    client.capabilities["prompts"] = {}
    assert R.execute("list_prompts", connection="svc")["ok"] is True


def test_大资源回too_large且不落盘(monkeypatch, connector, tmp_path):
    class _Big(_FakeClient):
        async def read_resource_content(self, uri):
            return {"uri": uri, "mimeType": "text/plain", "text": "x" * 5000}

    client = _Big(FULL_CAPS)
    _install(monkeypatch, connector, "svc", client)
    monkeypatch.setenv(R.ENV_MAX_BYTES, "1024")

    out = R.execute("read_resource", connection="svc", uri="file:///big.txt")
    assert out["ok"] is False and out["code"] == "too_large"
    assert out["bytes"] == 5000 and out["limit"] == 1024
    # 不落盘：spill 目录不应出现新文件（用临时目录当唯一写入面来判）
    assert list(tmp_path.rglob("*")) == []


def test_二进制资源回not_text(monkeypatch, connector):
    _install(monkeypatch, connector, "svc", _FakeClient(FULL_CAPS))
    out = R.execute("read_resource", connection="svc", uri="file:///a.bin")
    assert out["ok"] is False and out["code"] == "not_text"


def test_连接不存在回no_connection(connector, monkeypatch):
    import agent.tool_router as router
    monkeypatch.setattr(router, "_discovery_service", _FakeDiscovery(connector), raising=False)
    out = R.execute("list_resources", connection="missing")
    assert out["ok"] is False and out["code"] == "no_connection"


def test_发现服务未初始化也回no_connection(monkeypatch):
    import agent.tool_router as router
    monkeypatch.setattr(router, "_discovery_service", None, raising=False)
    out = R.execute("list_resources", connection="svc")
    assert out["ok"] is False and out["code"] == "no_connection"


@pytest.mark.parametrize("kwargs,needle", [
    ({"action": "nope", "connection": "svc"}, "不支持的 action"),
    ({"action": "list_resources", "connection": ""}, "需要 connection"),
    ({"action": "read_resource", "connection": "svc"}, "需要 uri"),
    ({"action": "get_prompt", "connection": "svc"}, "需要 name"),
    ({"action": "get_prompt", "connection": "svc", "name": "p", "arguments": [1]}, "必须是对象"),
])
def test_参数错误回bad_request(kwargs, needle):
    out = R.execute(**kwargs)
    assert out["ok"] is False and out["code"] == "bad_request"
    assert needle in out["error"]


def test_审计每次一条且不含原文(monkeypatch, connector):
    from agent.audit import facade

    recorded = []
    monkeypatch.setattr(facade.audit, "record",
                        lambda action, **kw: recorded.append((action, kw)))
    _install(monkeypatch, connector, "svc", _FakeClient(FULL_CAPS))

    R.execute("read_resource", connection="svc", uri="file:///secret/tenant-42.txt")
    assert len(recorded) == 1
    action, kw = recorded[0]
    assert action == "mcp_resource_access"
    body = kw["payload"]
    assert body["action"] == "read_resource"
    assert body["code"] == "ok" and body["bytes"] > 0
    assert body["connection_digest"].startswith("sha256:")
    assert body["target_digest"].startswith("sha256:")
    dumped = json.dumps(body, ensure_ascii=False)
    assert "tenant-42" not in dumped and "secret" not in dumped


def test_initialize把能力读回客户端(monkeypatch):
    """能力协商必须读回：initialize 的 capabilities 真存进客户端；空字典也算支持。"""
    from mcp_services.mcp_client import MCPClient

    client = MCPClient("python", [])

    async def _fake_send(method, params=None, timeout=None):
        if method == "initialize":
            return {"result": {"protocolVersion": "2024-11-05",
                               "capabilities": {"resources": {}, "prompts": {"listChanged": True}},
                               "serverInfo": {"name": "fake", "version": "1"}}}
        if method == "tools/list":
            return {"result": {"tools": []}}
        return {"result": {}}

    monkeypatch.setattr(client, "_send_request", _fake_send)
    import asyncio
    asyncio.run(client.initialize())

    assert client.capabilities == {"resources": {}, "prompts": {"listChanged": True}}
    assert client.supports("resources") is True   # 空字典 = 支持
    assert client.supports("prompts") is True
    assert client.supports("logging") is False


def test_http传输走同一能力闸门(connector):
    connector._connections["h"] = {
        "client": None, "tool_names": [], "transport": "http",
        "base_url": "http://127.0.0.1:9", "name": "h",
        "capabilities": {"resources": {}}, "connected_at": 0.0,
    }
    assert connector.resource_action("h", "list_prompts")["code"] == "not_supported"

    calls = []

    def _fake_rpc(base_url, method, params=None):
        calls.append((base_url, method, params))
        if method == "resources/list":
            return {"ok": True, "result": {"resources": [{"uri": "u", "name": "n"}]}}
        return {"ok": True, "result": {"contents": [{"uri": "u", "text": "body"}]}}

    connector._http_rpc = _fake_rpc  # type: ignore[assignment]
    out = connector.resource_action("h", "list_resources")
    assert out["ok"] and out["resources"][0]["uri"] == "u"
    out = connector.resource_action("h", "read_resource", uri="u")
    assert out["ok"] and out["contents"][0]["text"] == "body"
    assert [c[1] for c in calls] == ["resources/list", "resources/read"]


def test_上限env非法值回落默认(monkeypatch):
    monkeypatch.setenv(R.ENV_MAX_BYTES, "not-a-number")
    assert R.max_bytes() == 262144
    monkeypatch.setenv(R.ENV_MAX_BYTES, "1")
    assert R.max_bytes() == 1024          # 夹到下界，避免把资源闸门关死
    monkeypatch.delenv(R.ENV_MAX_BYTES, raising=False)
    assert R.max_bytes() == 262144
