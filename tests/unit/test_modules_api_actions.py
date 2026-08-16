#!/usr/bin/env python3
"""modules_api 统一干预接口专项测试：覆盖 ACTION_ROUTES 全部 24 种动作的边界情况

针对 DigitalLife 模块（action.digital_life，含 3 个高危紧急动作）与其他节点，
验证统一干预入口的边界行为：
  1. 24 种动作全部在 ACTION_ROUTES 注册且参数映射自洽
  2. 参数构建：const: 固定值防篡改 / 透传字段 / 缺字段跳过
  3. 高危动作（danger=high）reason 必填，缺失/空白 → 400
  4. 每个动作成功路径：_do_action 转发成功 → 200
  5. 未声明动作 / 未知模块 / 空 action → 400/404
  6. 限流拒绝 → 429；转发异常 → 502
  7. DigitalLife 专项：3 个紧急动作 action 参数固定为 stop/pause/network_block

运行: python -m pytest tests/unit/test_modules_api_actions.py -q
"""
import pytest
from flask import Flask, jsonify

import agent.modules_api as ma
from agent.modules_registry import ACTION_ROUTES, DOMAINS, get_node

# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════

@pytest.fixture
def app(monkeypatch):
    """最小 Flask app：注册 modules_bp + 为每个转发目标 URL 挂 fake 视图"""
    import agent.server_auth as sa
    monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)  # 测试隔离 token 拦截
    flask_app = Flask(__name__)
    flask_app.register_blueprint(ma.modules_bp)
    seen = set()
    for route in ACTION_ROUTES.values():
        if route.url in seen:
            continue
        seen.add(route.url)

        def _view(url=route.url):
            return jsonify({"ok": True, "url": url})

        # endpoint 名唯一化，避免重复 URL 冲突
        ep = "fake_" + "".join(c if c.isalnum() else "_" for c in route.url)
        flask_app.add_url_rule(route.url, ep, _view, methods=[route.method])
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def all_actions_node(monkeypatch):
    """fake 节点：声明全部 24 种动作，用于参数化成功路径"""
    class FakeNode:
        module_id = "test.all"
        actions = list(ACTION_ROUTES.keys())

    def _fake_get_node(mid):
        return FakeNode() if mid == "test.all" else get_node(mid)

    monkeypatch.setattr(ma, "get_node", _fake_get_node)
    return FakeNode


# ════════════════════════════════════════════════════════════
#  1. 注册表自洽性
# ════════════════════════════════════════════════════════════

def test_all_24_actions_registered():
    """ACTION_ROUTES 恰好包含 24 种动作（与 S1 注册表声明一致）"""
    assert len(ACTION_ROUTES) == 24
    # 每个动作的 params 值要么是透传字段名，要么是 const: 前缀
    for key, route in ACTION_ROUTES.items():
        for api_field, src in route.params.items():
            assert src or src.startswith("const:"), f"{key}.{api_field} 参数源为空"
        assert route.method == "POST", f"{key} 应为 POST"


def test_every_node_action_is_registered():
    """所有节点声明的动作都必须在 ACTION_ROUTES 中存在（防悬空引用）"""
    for domain in DOMAINS:
        for node in domain.nodes:
            for action in node.actions:
                assert action in ACTION_ROUTES, f"{node.module_id} 声明未注册动作 {action}"


# ════════════════════════════════════════════════════════════
#  2. 参数构建（const 防篡改 / 透传 / 缺字段）
# ════════════════════════════════════════════════════════════

def test_build_body_const_not_tamperable():
    """const: 固定值优先，前端同名入参无法篡改"""
    route = ACTION_ROUTES["emergency_stop"]
    body = ma._build_request_body(route, {"action": "cancel", "params": {}})
    assert body == {"action": "stop"}


def test_build_body_transfer_fields():
    """透传字段按前端入参填充"""
    route = ACTION_ROUTES["toggle_tool"]
    body = ma._build_request_body(route, {"name": "web_search", "enabled": True})
    assert body == {"name": "web_search", "enabled": True}


def test_build_body_missing_field_skipped():
    """缺字段时跳过，不写入 None/空串"""
    route = ACTION_ROUTES["toggle_tool"]
    body = ma._build_request_body(route, {})
    assert body == {}  # enabled 缺省不写入（由既有 API 默认处理）


# ════════════════════════════════════════════════════════════
#  3. 高危动作 reason 必填（后端兜底，前端二次确认之外的第二道防线）
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("action", [k for k, r in ACTION_ROUTES.items() if r.danger == "high"])
def test_high_danger_requires_reason(client, action, all_actions_node):
    """每个 high 动作：reason 缺失或纯空白 → 400"""
    r = client.post("/api/modules/test.all/actions",
                    json={"action": action, "params": {}})
    assert r.status_code == 400, f"{action} 缺 reason 应被拒绝"

    r = client.post("/api/modules/test.all/actions",
                    json={"action": action, "reason": "   ", "params": {}})
    assert r.status_code == 400, f"{action} 空白 reason 应被拒绝"


# ════════════════════════════════════════════════════════════
#  4. 全部 24 种动作成功路径
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("action", list(ACTION_ROUTES.keys()))
def test_all_actions_forward_ok(client, action, all_actions_node):
    """每个动作（含 reason，high 动作满足必填）转发成功 → 200 + ok"""
    payload = {"action": action, "reason": "自动化测试", "params": {}}
    r = client.post("/api/modules/test.all/actions", json=payload)
    assert r.status_code == 200, f"{action} 转发失败: {r.get_json()}"
    body = r.get_json()
    assert body["ok"] is True
    assert body["action"] == action
    assert body["forwarded"].startswith("POST ")


# ════════════════════════════════════════════════════════════
#  5. 边界：未声明动作 / 未知模块 / 空 action
# ════════════════════════════════════════════════════════════

def test_undeclared_action_rejected(client):
    """模块未声明的动作 → 400"""
    r = client.post("/api/modules/action.tools/actions",
                    json={"action": "emergency_stop", "reason": "x"})
    assert r.status_code == 400
    assert "未声明" in r.get_json()["error"]


def test_unknown_module_404(client):
    r = client.post("/api/modules/not.exist/actions",
                    json={"action": "toggle_tool", "reason": "x"})
    assert r.status_code == 404


def test_empty_action_400(client):
    r = client.post("/api/modules/action.tools/actions", json={})
    assert r.status_code == 400


# ════════════════════════════════════════════════════════════
#  6. 限流与转发异常
# ════════════════════════════════════════════════════════════

def test_rate_limit_429(client, monkeypatch, all_actions_node):
    """限流拒绝 → 429（REJECT 策略）"""
    class FakeLimiter:
        def check(self, **kw):
            return False
    monkeypatch.setattr(ma, "_rate_limiter", FakeLimiter())
    r = client.post("/api/modules/test.all/actions",
                    json={"action": "compress_memory", "reason": ""})
    assert r.status_code == 429


def test_forward_exception_502(client, monkeypatch, all_actions_node):
    """转发目标抛异常 → 502 + 审计记录失败"""
    def _boom(*a, **kw):
        raise RuntimeError("模拟转发故障")
    monkeypatch.setattr(ma, "_forward", _boom)
    r = client.post("/api/modules/test.all/actions",
                    json={"action": "compress_memory", "reason": ""})
    assert r.status_code == 502
    assert "转发失败" in r.get_json()["error"]


# ════════════════════════════════════════════════════════════
#  7. DigitalLife 模块专项（action.digital_life）
# ════════════════════════════════════════════════════════════

DIGITAL_LIFE_ACTIONS = {
    "emergency_stop": "stop",
    "emergency_pause": "pause",
    "block_network": "network_block",
}


def test_digital_life_declares_emergency_actions():
    """DigitalLife 节点恰好声明 3 个高危紧急动作"""
    node = get_node("action.digital_life")
    assert node is not None
    assert set(node.actions) == set(DIGITAL_LIFE_ACTIONS)
    assert node.danger == "high"


@pytest.mark.parametrize("action,fixed", DIGITAL_LIFE_ACTIONS.items())
def test_digital_life_action_const_fixed(client, action, fixed):
    """DigitalLife 紧急动作：action 参数后端固定，前端传什么都无效"""
    # 尝试篡改 action 值
    r = client.post("/api/modules/action.digital_life/actions",
                    json={"action": action, "reason": "专项测试",
                          "params": {"action": "cancel"}})
    assert r.status_code == 200
    result = r.get_json()
    # 转发到的 URL 与固定 action 值相符（通过 fake 视图无法直接断言 body，
    # 故在 _do_action 层用 monkeypatch 校验——见下一条）
    assert result["forwarded"] == f"POST /api/permission/emergency"


def test_digital_life_forward_body_fixed(monkeypatch, all_actions_node):
    """_do_action 层断言：emergency_stop 转发 body 的 action 恒为 stop"""
    captured = {}

    def _fake_forward(action_key, body):
        captured["action_key"] = action_key
        captured["body"] = body
        return {"ok": True}

    monkeypatch.setattr(ma, "_forward", _fake_forward)
    payload, status = ma._do_action("test.all", "emergency_stop",
                                    {"action": "cancel"}, "专项测试")
    assert status == 200
    assert captured["body"] == {"action": "stop"}


def test_digital_life_action_audit_written(monkeypatch, all_actions_node):
    """干预后审计记录写入 _audit_history（可回溯）"""
    monkeypatch.setattr(ma, "_forward", lambda *a, **k: {"ok": True})
    ma._do_action("test.all", "compress_memory", {}, "审计验证")
    records = [r for r in ma._audit_history if r["module_id"] == "test.all"]
    assert records, "应产生审计记录"
    assert records[-1]["action"] == "module_action.compress_memory"
    assert records[-1]["ok"] is True
