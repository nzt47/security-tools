"""统一响应信封 + RFC 9457 错误模型守卫（阶段 2 / R3 · 审计 H-3）。

【解决什么】2026-10-03 审计实测：后端成功结构 ≥ 5 种、错误结构 ≥ 6 种，
且 **0 个 @app.errorhandler** ⇒ API 面的 404/405/500 一律回落 Flask 的 HTML 页面。
前端两套客户端都在按 JSON 解析，于是"测试绿、线上炸"。
本文件把「API 面的错误只有一种结构」变成机械判定（验收 A6 / 成功指标 K3）。

【关于验收 A6 的字面写法】审计报告写的判据是
    grep -rc "@app.errorhandler" app_server.py  >= 1
本实现**没有**在 app_server.py 里写 @app.errorhandler 装饰器，而是调用
`agent.api_envelope.install_error_handlers(app)` —— 因为要满足三件事：
  ① 错误形状只有**一处**实现（在 app_server 里写装饰器就等于把形状写进大文件，
     与"契约事实源唯一化"的方向相反）；
  ② 可**纯单测**（本文件不需要导入 app_server，那要 50s+ 且会重复注册 Prometheus）；
  ③ 可用 YUNSHU_RFC9457_ERRORS 整段关闭（审计的回滚要求）。
故 A6 的**意图**（"errorhandler 已注册"）由更强的断言保证：
  · test_app_server_确实装载了错误模型 —— 源码级：app_server 调用了 installer；
  · Test错误形状唯一 —— 行为级：/api 的 404/405/500 全部是 application/problem+json
    且字段集合逐一相同（这正是 K3 要的"1 种结构"）。
字面 grep 与意图在此不一致，如实记录，不以"改个 grep"蒙混。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from flask import Flask

from agent.api_envelope import (
    ENVELOPE_HEADER,
    ENVELOPE_VERSION,
    PROBLEM_CONTENT_TYPE,
    install_error_handlers,
    ok,
    problem,
)

ROOT = Path(__file__).resolve().parents[2]

#: RFC 9457 的五个标准成员。
RFC9457_CORE = {"type", "title", "status", "detail", "instance"}


@pytest.fixture()
def app():
    a = Flask("envelope_under_test")
    install_error_handlers(a, enabled=True)

    @a.route("/api/ok")
    def _ok():
        return ok({"n": 1}, message="fine", meta={"total": 1})

    @a.route("/api/boom")
    def _boom():
        raise RuntimeError("kaboom")

    @a.route("/api/only-post", methods=["POST"])
    def _only_post():
        return ok()

    @a.route("/page")
    def _page():
        return "page"

    a.config["PROPAGATE_EXCEPTIONS"] = False  # 让 Exception handler 有机会接管
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


class Test成功信封:
    def test_形状与既有前端契约一致(self, client):
        """成功体沿用 {code, data, message} —— 前端 utils/request.ts 已在按它解包。"""
        body = client.get("/api/ok").get_json()
        assert body["code"] == 200
        assert body["data"] == {"n": 1}
        assert body["message"] == "fine"
        assert body["meta"] == {"total": 1}

    def test_带信封版本头(self, client):
        resp = client.get("/api/ok")
        assert resp.headers.get(ENVELOPE_HEADER) == ENVELOPE_VERSION

    def test_无_meta_时不出现_meta_键(self, client):
        """可选键不得凭空出现 —— 前端做键存在性判断时会误判。"""
        app = Flask("no_meta")
        with app.test_request_context("/api/x"):
            body = json.loads(ok().get_data(as_text=True))
        assert "meta" not in body

    def test_自定义状态码(self):
        app = Flask("created")
        with app.test_request_context("/api/x"):
            resp = ok({"id": 7}, status=201)
        assert resp.status_code == 201
        assert json.loads(resp.get_data(as_text=True))["code"] == 201


class Test错误模型:
    def test_是_problem_json(self, client):
        resp = client.get("/api/definitely-missing")
        assert resp.status_code == 404
        assert resp.mimetype == PROBLEM_CONTENT_TYPE

    def test_含_rfc9457_五个核心成员(self, client):
        body = client.get("/api/definitely-missing").get_json()
        assert RFC9457_CORE <= set(body), sorted(body)

    def test_status_字段与_http_状态码一致(self, client):
        """RFC 9457 明确要求两者一致（不一致是最常见的误实现）。"""
        resp = client.get("/api/definitely-missing")
        assert resp.get_json()["status"] == resp.status_code == 404

    def test_detail_是中文而不是_werkzeug_英文默认值(self, client):
        """同一响应体不得混两种语言：title 是中文，detail 也必须是中文。

        实测过：此前 detail 直接取 werkzeug 的 exc.description，
        404 得到 "The requested URL was not found on the server..."。
        """
        body = client.get("/api/definitely-missing").get_json()
        assert body["detail"] == "请求的资源不存在。"
        assert not re.search(r"[A-Za-z]{4,}\s+[A-Za-z]{3,}", body["detail"]), body["detail"]

    def test_instance_指向本次请求(self, client):
        body = client.get("/api/definitely-missing").get_json()
        assert body["instance"] == "/api/definitely-missing"

    def test_title_不含本次请求细节(self, client):
        """title 是**类型**的人读名（可复用、可翻译），detail 才是本次实例。"""
        body = client.get("/api/definitely-missing").get_json()
        assert body["title"] == "资源不存在"
        assert "/api/definitely-missing" not in body["title"]

    def test_type_是_uri(self, client):
        body = client.get("/api/definitely-missing").get_json()
        assert re.match(r"^https?://", body["type"]), body["type"]

    def test_错误响应也带信封版本头(self, client):
        assert client.get("/api/definitely-missing").headers.get(ENVELOPE_HEADER) == ENVELOPE_VERSION

    def test_405_走同一形状(self, client):
        resp = client.get("/api/only-post")
        assert resp.status_code == 405
        assert resp.mimetype == PROBLEM_CONTENT_TYPE
        assert RFC9457_CORE <= set(resp.get_json())

    def test_500_走同一形状(self, client):
        resp = client.get("/api/boom")
        assert resp.status_code == 500
        assert resp.mimetype == PROBLEM_CONTENT_TYPE
        body = resp.get_json()
        assert RFC9457_CORE <= set(body)
        # 【不泄漏内部信息】detail 是固定文案，不含异常消息与堆栈
        assert "kaboom" not in json.dumps(body, ensure_ascii=False)

    def test_errors_扩展成员(self):
        app = Flask("with_errors")
        with app.test_request_context("/api/x"):
            resp = problem(422, errors=[{"field": "name", "detail": "必填"}])
        body = json.loads(resp.get_data(as_text=True))
        assert body["errors"] == [{"field": "name", "detail": "必填"}]

    def test_未知状态码有兜底(self):
        app = Flask("teapot")
        with app.test_request_context("/api/x"):
            resp = problem(418)
        body = json.loads(resp.get_data(as_text=True))
        assert body["status"] == 418
        assert body["title"]  # 不得为空


class Test错误形状唯一:
    """K3 的可执行形式：API 面所有错误响应**字段集合完全相同**。"""

    def test_404_405_500_字段集合一致(self, client):
        shapes = set()
        for path in ("/api/definitely-missing", "/api/only-post"):
            shapes.add(frozenset(client.get(path).get_json()))
        shapes.add(frozenset(client.get("/api/boom").get_json()))
        assert len(shapes) == 1, "API 面存在多种错误结构：" + str([sorted(s) for s in shapes])
        assert RFC9457_CORE <= set(next(iter(shapes)))

    def test_错误体里没有裸_error_键(self, client):
        """{"error": ...} 是本仓错误结构混乱的代表形态；新形状里不得再出现。"""
        body = client.get("/api/definitely-missing").get_json()
        assert "error" not in body
        assert "ok" not in body


class Test浏览器语义不受影响:
    def test_页面_404_仍是_HTML(self, client):
        """非 /api 前缀保持 Flask 默认 —— 浏览器取缺失页面收 HTML 才是对的。"""
        resp = client.get("/page/definitely-missing")
        assert resp.status_code == 404
        assert "text/html" in resp.mimetype


class Test开关:
    def test_关闭后回落默认(self):
        a = Flask("disabled")
        info = install_error_handlers(a, enabled=False)
        assert info["installed"] is False
        c = a.test_client()
        resp = c.get("/api/definitely-missing")
        assert resp.status_code == 404
        assert resp.mimetype != PROBLEM_CONTENT_TYPE, "关闭后不应再产出 problem+json"

    def test_开启信息可读(self):
        a = Flask("enabled")
        info = install_error_handlers(a, enabled=True)
        assert info["installed"] is True
        assert info["scope"] == "/api/"
        assert 404 in info["statuses"] and 500 in info["statuses"]


class Testapp_server_确实装载:
    """源码级锚点：光有模块不算数，app_server 必须真的装上。"""

    SRC = ROOT / "app_server.py"

    def test_调用了安装函数(self):
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        assert "install_error_handlers" in src, (
            "app_server.py 未装载 RFC 9457 错误模型 —— API 面的 404/405/500 会回落 HTML"
        )

    def test_安装被_try_包裹不阻断启动(self):
        """与其他降级装配一致：装不上只告警，不得让服务起不来。"""
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        idx = src.index("install_error_handlers")
        window = src[max(0, idx - 900): idx + 400]
        assert "try:" in window and "except Exception" in window, (
            "错误模型装载未做降级保护：它一旦抛异常会直接把服务挡在启动之外"
        )
