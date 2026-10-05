"""P1-front **第五批**：全景/监控四个端点的显式信封契约（2026-10-05）。

【本批端点】都是「单端点、单消费方」，且集中在同一片页面（panorama/）：
  · GET /api/panorama          → pages/hub/panorama/sensors.tsx（裸对象，实测 31 键）
  · GET /api/sensors           → pages/hub/panorama/sensors.tsx（**裸数组**，实测 18 项）
  · GET /api/health/dashboard  → pages/hub/panorama/monitor.tsx
  · GET /api/status            → pages/hub/panorama/monitor.tsx（取到后只存不渲染）

【动手前扫出来的**一个会静默改坏的消费方**（本批最该被复核的地方）】
app_server._provider_panorama() **进程内**调用 plugins.status.api_panorama()，再直接读响应的
**顶层键**（sensor_on / sensor_total / health）拼成模块拓扑节点的指标 chip。
视图一旦改成 ok()，这些键就搬进了 data ⇒ 不改它就会 **静默拿到 None**
（拓扑节点的 chip 全空，不报错、日志也没有）。⇒ 同批改走 panorama_provider_payload()，
并把该投影从 app_server 搬到插件里（app_server 导入 80–100s，守卫里测不到这段）。

【其余消费方也扫过了（结论写下来，免得下一个人重扫）】
  · health_check.py 与 tests/test_observability_e2e.py —— 只断言**状态码 200**，不读响应体 ⇒ 不受影响；
  · app_server._provider_sensors/_provider_status —— 调的是 _Yunshu 的内部方法而不是视图 ⇒ 不受影响；
  · agent/modules_api.py 的 /api/health/dashboard provider 走 get_probe_overview()（纯函数）⇒ 不受影响；
  · 全仓 tests/ 与 yunshu-ui/ 对这四个端点的响应体做**顶层键访问**的地方：实测 0 处。

【为什么不导入 app_server】见第二批守卫的头注：该导入本机 80–100s，在 CI 覆盖率分片下
超 300s 预算，实测把 Shard 4/6 拖红。这里用最小 Flask app + 替身，接线用 AST 断言。
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

_SENSOR_READING = {
    "sensor_name": "cpu",
    "description": "CPU 使用率",
    "value": 12.5,
    "severity": "normal",
    "enabled": True,
}


@pytest.fixture
def batch5_client(monkeypatch):
    """最小 Flask app：status 插件 + 健康看板蓝图；两个延迟 import 用替身顶掉。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    fake_app_server = types.ModuleType("app_server")

    class _Body:
        @staticmethod
        def get_sensor_info():
            return [dict(_SENSOR_READING)]

    class _Yunshu:
        body = _Body()

        @staticmethod
        def get_status():
            return {"云枢": "ok", "系统": {"cpu": 1}}

    fake_app_server._Yunshu = _Yunshu
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    # 健康看板：assess() 会真的跑评分器，这里换成最小替身（本文件测的是**响应契约**）
    health_mod = importlib.import_module("agent.health.dashboard")

    class _Assessment:
        overall = 0.91
        dimensions = {"error_rate": 0.0, "response_time": 0.2, "tool_success": 0.98}
        issues = [{"sensor_name": "disk", "severity": "warning", "score": 0.7}]

    class _Assessor:
        @staticmethod
        def assess():
            return _Assessment()

        @staticmethod
        def get_history(limit=10):
            return []

    monkeypatch.setattr(health_mod, "health_assessor", _Assessor())

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    status_plugin = importlib.import_module("plugins.status")
    app = Flask(__name__)
    app.register_blueprint(status_plugin.bp)
    app.register_blueprint(health_mod.health_bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


def _envelope_data(client, path):
    """打一次真实请求，断言信封头与业务码，返回 data。"""
    resp = client.get(path)
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。\n"
        "另注意：/api/sensors、/api/status、/api/panorama 在 agent/server_routes/routes_panorama.py "
        "还有一份**死副本**，活体在 plugins/status.py。"
    )
    body = resp.get_json()
    assert body.get("code") == 200, repr(body)[:200]
    return body.get("data")


class Test传感器列表:
    def test_信封与载荷形状(self, batch5_client):
        """GET /api/sensors：data 必须是**数组本身**（迁移前就是裸数组）。"""
        data = _envelope_data(batch5_client, "/api/sensors")
        assert isinstance(data, list), (
            "data 应为数组，实测 " + repr(type(data).__name__) + "，值=" + repr(data)[:200]
        )
        assert data and data[0].get("sensor_name") == "cpu", (
            "数组元素没带上替身给的读数 ⇒ 载荷被换了形状：实测 " + repr(data)[:200]
        )


class Test运行状态:
    def test_信封与载荷形状(self, batch5_client):
        """GET /api/status：data 仍是 get_status() 那个对象（既有键一个不少）。"""
        data = _envelope_data(batch5_client, "/api/status")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("云枢", "系统"):
            assert key in data, (
                "状态载荷缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。实测键："
                + repr(sorted(data.keys()))
            )


class Test健康看板:
    def test_信封与载荷形状(self, batch5_client):
        """GET /api/health/dashboard：data 仍是那四个键。"""
        data = _envelope_data(batch5_client, "/api/health/dashboard")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("overall_health", "dimensions", "issues", "history"):
            assert key in data, (
                "健康看板缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。实测键："
                + repr(sorted(data.keys()))
            )


class Test全景_provider投影:
    """panorama_provider_payload() —— 那个「不改就会静默改坏」的消费方。"""

    def test_信封体给出正确的指标_chip(self):
        from plugins.status import panorama_provider_payload
        body = {
            "code": 200,
            "message": "",
            "data": {
                "sensor_on": 7,
                "sensor_total": 9,
                "health": [
                    {"sensor_name": "cpu", "value": 33.3},
                    {"sensor_name": "memory", "value": 44.4},
                    {"sensor_name": "battery", "value": None},  # 无值不落 chip
                ],
            },
        }
        out = panorama_provider_payload(body)
        assert out == {"sensor_on": 7, "sensor_total": 9, "cpu": 33.3, "memory": 44.4}, (
            "投影结果不对：实测 " + repr(out) + " —— 拓扑节点的指标 chip 会因此缺项或全空。"
        )

    def test_裸体必须响亮失败而不是静默给空(self):
        """反向：视图若被改回裸体，投影必须**抛错**（不得静默返回空 chip）。

        【为什么单钉这一条】这正是本批要防的缺陷形态：data.get("sensor_on") 在裸体上
        返回 None，而调用方的 except Exception 又会把它降级成「节点离线」—— 全程无报错。
        """
        from plugins.status import panorama_provider_payload
        with pytest.raises(ValueError):
            panorama_provider_payload({"sensor_on": 7, "sensor_total": 9, "health": []})

    def test_unwrap_enveloped_两个方向(self):
        from agent.api_envelope import unwrap_enveloped
        assert unwrap_enveloped({"code": 200, "data": {"a": 1}, "message": ""}) == {"a": 1}
        for bad in ({"a": 1}, {}, None, [1, 2], {"code": 200}):
            with pytest.raises(ValueError):
                unwrap_enveloped(bad)


class Test视图必须经信封出口:
    """结构断言（AST）—— 覆盖 /api/panorama：它的载荷要拉十几个 _Yunshu 子系统，
    在守卫里执行等于把 80–100s 的 app_server 导入拉回来，故用**结构**而不是行为来钉。

    【为什么用 AST 而不是文本切片】文本断言会把函数外/相邻分支的内容算进来（本仓有过假红的先例）；
    AST 表达的是「这个函数的 return 语句调用了谁」这一结构事实。
    """

    @staticmethod
    def _func_node(path: Path, func_name: str):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return node
        raise AssertionError(path.name + " 里找不到函数 " + func_name + " —— 视图被重命名/删除了？")

    @pytest.mark.parametrize(
        "rel_path,func_name",
        [
            ("plugins/status.py", "api_sensors"),
            ("plugins/status.py", "api_status"),
            ("plugins/status.py", "api_panorama"),
            ("agent/health/dashboard.py", "dashboard"),
        ],
    )
    def test_视图的_return_走_ok(self, rel_path, func_name):
        fn = self._func_node(ROOT / rel_path, func_name)
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
        assert returns, rel_path + "::" + func_name + " 没有任何 return —— 结构变了，请复核本断言。"
        for ret in returns:
            value = ret.value
            name = value.func.id if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) else None
            assert name in ("_ok", "ok"), (
                rel_path + "::" + func_name + " 的 return 没有经过统一信封出口（实测 "
                + (name or type(value).__name__)
                + "）⇒ 该端点会静默退回非信封形态，前端 getEnvelope 会直接抛错。"
            )
        calls = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "jsonify" not in calls, (
            rel_path + "::" + func_name + " 里又出现了 jsonify —— "
            "迁移被部分回退（信封出口不唯一会让前端读到两种形态）。"
        )
