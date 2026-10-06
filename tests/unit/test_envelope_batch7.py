"""P1-front **第七批**：上下文档位 / 技能列表两个端点，以及 **K4 的终点**（启发式已被删除）。

【本批端点】
  · GET /api/context/status → components/workbench/panels/ContextManagerBar.tsx
                            **与** lib/contextMonitorApi.ts（**两个消费方、两套客户端**）
  · GET /api/skills         → pages/hub/memory/skills.tsx

【本批为什么是最后一批】迁完之后 yunshu-ui 里 pickObj/pickList 的调用点归零，
两个**启发式** helper 已从 pages/hub/components/ui.tsx 删除（见同批提交）。
本文件只钉两个端点的契约；「helper 确实不在了」由
tests/unit/test_frontend_pick_helpers_removed.py 单独钉（先剥注释再计数）。

【为什么这个端点要特别小心（本批唯一的结构性风险）】
/api/context/status 有**两个**消费方，且它们走的是**两套客户端**：
  · ContextManagerBar.tsx 用 hubGet（自己发请求）→ 本批改 getEnvelope；
  · lib/contextMonitorApi.ts 用 lib/apiClient.request()，它**不拆信封、返回原始体**
    → 本批改 unwrapEnvelopeBody（拿已解析的体来拆）。
只改前者的话，后者会在 {code,data,message} 上读 percentage/status_level 而**静默拿到 undefined**
（面板不报错，只是数字变空）—— 这正是本仓反复记录的「迁移信封夹带契约变更」形态。

【为什么不导入 app_server】见第二批守卫的头注（本机 80–100s，CI 覆盖率分片下超 300s 预算）。
这里复用本仓既有的「最小 Flask app + 桩 app_server」技法（与
tests/unit/test_context_limits_alignment.py 的 panel_env 同源，此处只保留契约断言需要的最小集）。
"""
from __future__ import annotations

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


class _Counter:
    @staticmethod
    def count(text: str) -> int:
        return len(text or "")


class _SessionMgr:
    @staticmethod
    def get_messages(session_id, limit=0):
        return [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]


class _Memory:
    compress_rounds = 2


class _Yunshu:
    _memory = _Memory()

    @staticmethod
    def context_limit_info():
        return {"limit_tokens": 1000, "limit_source": "stub", "available": True}


class _Cfg:
    @staticmethod
    def get(section, key, default=None):
        return default


@pytest.fixture
def ctx_client(monkeypatch):
    """最小 app：plugins.memory 蓝图 + 桩 app_server（返回 (client, )）。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    fake = types.ModuleType("app_server")
    fake._Yunshu = _Yunshu()
    fake._session_mgr = _SessionMgr()
    fake._get_current_session_id = lambda: "sess-1"
    fake._get_token_counter = lambda: _Counter()
    fake._cfg = _Cfg()
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    mem = importlib.import_module("plugins.memory")
    monkeypatch.setattr(mem, "_context_limit_info", lambda _y: _Yunshu.context_limit_info())
    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def skills_client(monkeypatch):
    """最小 app：plugins.skills 蓝图 + 桩 _skills_mgr。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    class _SkillsMgr:
        @staticmethod
        def get_all():
            return [{"id": "demo-skill", "name": "演示技能", "enabled": True}]

    fake = types.ModuleType("app_server")
    fake._skills_mgr = _SkillsMgr()
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    skills = importlib.import_module("plugins.skills")
    app = Flask(__name__)
    app.register_blueprint(skills.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


def _envelope_data(client, path):
    resp = client.get(path)
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。\n"
        "另注意：/api/context/status 的第二个消费方 lib/contextMonitorApi.ts 走 "
        "apiClient.request()（不拆信封），它靠 unwrapEnvelopeBody 显式拆 —— 端点头没了它会抛错。"
    )
    body = resp.get_json()
    assert body.get("code") == 200, repr(body)[:200]
    return body.get("data")


class Test上下文档位:
    def test_信封与载荷形状(self, ctx_client):
        data = _envelope_data(ctx_client, "/api/context/status")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("current_tokens", "token_limit", "percentage", "status_level",
                    "status_reasons", "recent_messages", "send_limit_semantics"):
            assert key in data, (
                "上下文档位缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。实测键："
                + repr(sorted(data.keys()))
            )
        # 桩给的是 1000 窗口、两条消息共 10 字符 ⇒ 占用必须被真实算出来（不是恒 0）
        assert data["token_limit"] == 1000
        assert data["percentage"] == 1.0


class Test技能列表:
    def test_信封与载荷形状(self, skills_client):
        data = _envelope_data(skills_client, "/api/skills")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("installed", "available"):
            assert key in data, (
                "技能载荷缺少既有键「" + key + "」⇒ 前端读不到列表。实测键："
                + repr(sorted(data.keys()))
            )
        assert isinstance(data["installed"], list)
        assert any(s.get("id") == "demo-skill" for s in data["installed"]), (
            "桩给的技能没出现在 installed 里 —— 载荷被换了形状：实测 " + repr(data["installed"])[:200]
        )


class Test视图必须经信封出口:
    """结构断言（AST）—— 与第五批同源：文本断言会把相邻分支算进来，AST 只表达「return 调用了谁」。"""

    @staticmethod
    def _func_node(path: Path, func_name: str):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return node
        raise AssertionError(path.name + " 里找不到函数 " + func_name + " —— 视图被重命名/删除了？")

    @staticmethod
    def _own_returns(fn):
        """只取**该函数自己**的 return，不钻进嵌套函数。

        【为什么必须排除嵌套函数】plugins/skills.py::api_skills_get 里定义了一个内层闭包
        `_asset_fallback`，它有自己的 `return fallback_cls` —— 用 ast.walk 会把那条也算进来，
        于是「视图的 return 必须经 _ok」这条断言会在**完全正确**的代码上变红（实测踩到）。
        """
        found = []

        def _visit(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                if isinstance(child, ast.Return) and child.value is not None:
                    found.append(child)
                _visit(child)

        _visit(fn)
        return found

    @pytest.mark.parametrize(
        "rel_path,func_name",
        [
            ("plugins/memory.py", "api_context_status"),
            ("plugins/skills.py", "api_skills_get"),
        ],
    )
    def test_视图的_return_走_ok(self, rel_path, func_name):
        fn = self._func_node(ROOT / rel_path, func_name)
        returns = self._own_returns(fn)
        assert returns, rel_path + "::" + func_name + " 没有任何 return —— 结构变了，请复核本断言。"
        for ret in returns:
            value = ret.value
            name = value.func.id if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) else None
            assert name in ("_ok", "ok"), (
                rel_path + "::" + func_name + " 的 return 没有经过统一信封出口（实测 "
                + (name or type(value).__name__)
                + "）⇒ 该端点会静默退回非信封形态，前端的显式解析会直接抛错。"
            )
        calls = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "jsonify" not in calls, (
            rel_path + "::" + func_name + " 里又出现了 jsonify —— 迁移被部分回退。"
        )
