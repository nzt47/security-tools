"""心跳端点的**显式信封契约**守卫（P1-front 第一批 · 2026-10-04）。

【解决什么 —— 这是 P1-front 的"第一块地基"】
前端页面层的 `hubGet` 依赖 `pickObj/pickList` **猜**响应形态。要删掉这个启发式，
前置是"被猜的那些端点先以 `X-Envelope` 显式声明形态"。本文件把**第一批**（心跳）
的这条契约钉住，使它可以被机械复核，而不是靠"我改过了"。

【为什么先做心跳】它的消费面只有 `pages/hub/engine/heartbeat.tsx` 一个文件，
且该页注释里已写明真实形状（`{history, limit, offset, total}`），改写时无需语义猜测。

【为什么用真实 app 而不是自建 Flask 应用】
自建应用只能证明"信封模块本身能工作"（那已被 `test_api_envelope.py` 覆盖），
证明不了**这条路由接上了它**。本仓已记录过"模块已实现 + 单测全绿 ≠ 机制已生效"
（`ok()` 曾有 23 条单测而全仓 0 个调用点）。故此处以运行期 `url_map` 为准。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(scope="module")
def client():
    """真实 app 的测试客户端。

    【为什么 module 作用域】`import app_server` 实测 80–100s，逐用例导入不可接受。
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import app_server
    app_server.app.config["TESTING"] = True
    return app_server.app.test_client()


#: 本批已迁移到显式信封的心跳端点。
MIGRATED = ["/api/heartbeat", "/api/heartbeat/history", "/api/heartbeat/status"]


class Test心跳端点已声明信封:
    def test_成功响应带_X_Envelope_头(self, client):
        """**本批的核心断言**：成功路径必须带信封版本头。

        【方向说明】这条失败有两个方向，消息里都要写到：
          · 没有头 ⇒ 该端点没接上 `ok()`（迁移被回退，或改错了文件）；
          · 不是 v2 ⇒ 信封版本变了，前端解析要跟着改。
        """
        missing = []
        wrong = []
        for ep in MIGRATED:
            resp = client.get(ep)
            got = resp.headers.get("X-Envelope")
            if got is None:
                missing.append(ep + " (HTTP " + str(resp.status_code) + ")")
            elif got != "v2":
                wrong.append(ep + " -> " + str(got))
        assert not missing, (
            "以下端点**成功/失败响应上没有 X-Envelope 头** ⇒ 它们没有走 ok()/problem()。\n  "
            + "\n  ".join(missing)
            + "\n注意：这三个端点的**活体实现**在 plugins/status.py（"
              "agent/server_routes/routes_monitoring.py 里那份是 KNOWN_UNREGISTERED 死代码，"
              "改它不会生效）。"
        )
        assert not wrong, (
            "信封版本不是 v2 ⇒ 前端解析契约要同步更新。实测：" + repr(wrong)
        )

    def test_心跳体仍是既有的业务键(self, client):
        """迁移信封**不得**改变业务载荷（形状不变，只是被装进 data）。

        防的是"顺手改了契约"：前端与第三方消费的是 `status`/`checks`/`timestamp`。
        """
        resp = client.get("/api/heartbeat")
        body = resp.get_json()
        assert isinstance(body, dict), "响应不是 JSON 对象：" + repr(body)[:200]
        assert body.get("code") == 200, "成功体的 code 应为 200，实测 " + repr(body.get("code"))
        data = body.get("data")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("status", "checks", "timestamp"):
            assert key in data, (
                "心跳载荷缺少既有业务键「" + key + "」⇒ 迁移信封时改变了契约。"
                "实测键：" + repr(sorted(data.keys()))
            )
        assert isinstance(data["checks"], dict), "checks 应为对象"

    def test_历史端点的分页语义未被信封吞掉(self, client):
        """`/api/heartbeat/history` 的分页字段必须仍在**业务载荷**里可取到。

        【为什么要单独钉】该端点返回 `{history,total,limit,offset}`，前端用
        `pickList(r,'history')` 取。装了信封之后这些键的位置会变 ——
        若只把 `history` 放进 data 而把 total/limit/offset 丢到 envelope 层，
        取分页的地方就会**静默拿到 undefined**。
        """
        resp = client.get("/api/heartbeat/history?limit=5")
        body = resp.get_json()
        assert body.get("code") == 200, repr(body)[:200]
        data = body.get("data")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("history", "total", "limit", "offset"):
            assert key in data, (
                "历史载荷缺少分页键「" + key + "」⇒ 分页语义被信封吞掉。"
                "实测键：" + repr(sorted(data.keys()))
            )
        assert isinstance(data["history"], list)
        assert data["limit"] == 5, "limit 未被透传，实测 " + repr(data.get("limit"))

    def test_扫描口径覆盖全集(self, client):
        """防止本文件被"改窄"成只看部分端点而静默通过。"""
        import app_server
        live = {str(r.rule) for r in app_server.app.url_map.iter_rules()}
        for ep in MIGRATED:
            assert ep in live, (
                "被断言的端点「" + ep + "」在运行期 url_map 里不存在 —— "
                "要么路由被删/改名（那就该更新 MIGRATED），要么本测试已与事实脱节。"
            )
