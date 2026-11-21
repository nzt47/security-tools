"""GET /api/cognitive/reject 必须存在于 app_server 侧（2026-11-21 补）。

【为什么补这个端点】UI「运行监控 → 认知闭环」面板（CognitiveLoopPanel）把
/api/cognitive/status 与 /api/cognitive/reject **并排取**（Promise.allSettled）；
app_server 只挂了 status（plugins/status.py），reject 只在**独立传感器服务**
（cognitive/flask_adapter.py，随 sensor_server 挂载）里 —— 于是真机每页一条 404，
「驳回判定」一栏永远空，而且**不报错**（两个都失败才报）⇒ 静默少一栏。

覆盖度产物 reports/route_ui_coverage.json 把它算成「已接入」（它扫到了
flask_adapter.py 里的 @app.route）—— 这正是本仓那条「覆盖 ≠ 可用」的实例：
静态能看到路由，不等于 app_server 挂上了它。

【本文件钉三件事】
1. **活体路由存在**（最小 Flask app + 替身，不导入 app_server：该导入本机 80–100s，
   CI 覆盖率分片会超预算）；
2. **口径与 status 同源**：读数走 body.collect_quick() → to_dict()，裁决走
   PromptInjector.should_reject_task —— 不复制判据、不自己猜 severity；
3. **载荷形状** {rejected: bool, reason: str} 与 UI 的消费方式一致（UI 只读这两个键）。
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def reject_client(monkeypatch):
    """最小 Flask app + app_server 替身（只给 view 真正用到的那几个属性）。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    class _Reading:
        def __init__(self, severity: str, value: float):
            self._d = {"sensor_name": "cpu", "severity": severity, "value": value}

        def to_dict(self) -> dict:
            return dict(self._d)

    class _Body:
        def __init__(self):
            self.calls = 0

        def collect_quick(self):
            self.calls += 1
            return [_Reading("critical", 95.0)]

    class _Profile:
        label = "normal"
        description = "一切正常"

    class _Behavior:
        profile = _Profile()

    class _Injector:
        def __init__(self):
            self.reject_calls = []
            self.summary_calls = []

        def get_summary(self, readings):
            self.summary_calls.append(readings)
            return "CPU 95%"

        def should_reject_task(self, readings):
            self.reject_calls.append(readings)
            if any(r.get("severity") == "critical" for r in readings):
                return (True, "身体出现严重不适：CPU 过热")
            return (False, "一切正常，随时待命")

    class _Yunshu:
        body = _Body()
        _injector = _Injector()
        _behavior = _Behavior()

        @staticmethod
        def _build_body_status(readings):
            return {"readings": [r.to_dict() for r in readings]}

    fake = types.ModuleType("app_server")
    fake._Yunshu = _Yunshu
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from flask import Flask

    status = importlib.import_module("plugins.status")
    app = Flask(__name__)
    app.register_blueprint(status.bp)
    app.config["TESTING"] = True
    return app.test_client(), _Yunshu


class Test认知裁决端点:
    def test_路由挂在_status_蓝图上且返回_rejected_reason(self, reject_client):
        client, yunshu = reject_client
        resp = client.get("/api/cognitive/reject")
        assert resp.status_code == 200, "app_server 侧没有这条路由 ⇒ UI 每页一条 404（实测）"
        body = resp.get_json()
        assert set(body) == {"rejected", "reason"}, "契约是多出来的键会误导消费方：实测 " + repr(body)
        assert body["rejected"] is True, "critical 读数必须判为拒绝：实测 " + repr(body)
        assert isinstance(body["reason"], str) and body["reason"], "reason 必须是非空字符串：实测 " + repr(body)

    def test_读数口径与_status_完全同源(self, reject_client):
        """两处都必须把 body.collect_quick() → to_dict() 交给 injector（同一份数）。"""
        client, yunshu = reject_client
        client.get("/api/cognitive/status")
        client.get("/api/cognitive/reject")
        assert yunshu._injector.summary_calls, "status 没经过 injector.get_summary"
        assert yunshu._injector.reject_calls, "reject 没经过 injector.should_reject_task"
        assert yunshu._injector.summary_calls[:1] == yunshu._injector.reject_calls[:1], (
            "status 与 reject 拿到的读数不一致 —— 口径分叉会让两个数字互相打架：实测 "
            + repr(yunshu._injector.summary_calls[:1]) + " vs " + repr(yunshu._injector.reject_calls[:1])
        )
        assert yunshu._injector.reject_calls[0][0]["severity"] == "critical", (
            "reject 拿到的是 to_dict() 之后的字典（severity 是判据键）：实测 "
            + repr(yunshu._injector.reject_calls[0])
        )


class Test前后端字面量一致:
    def test_前端常量与后端路由是同一个路径(self):
        """改路径要两边同改，否则 UI 又回到 404（本轮就是这么缺的）。"""
        endpoints = (ROOT / "yunshu-ui" / "src" / "api" / "endpoints.ts").read_text(encoding="utf-8")
        assert "COGNITIVE_REJECT = '/api/cognitive/reject'" in endpoints, (
            "前端 COGNITIVE_REJECT 的字面量变了 —— 后端路由也要同改"
        )
        src = (ROOT / "plugins" / "status.py").read_text(encoding="utf-8")
        assert '@bp.route("/api/cognitive/reject")' in src, (
            "plugins/status.py 里没有这条路由（活体在 app_server 侧，flask_adapter 那份只随传感器服务挂）"
        )
