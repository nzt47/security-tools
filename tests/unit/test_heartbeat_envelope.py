"""心跳端点的**显式信封契约**守卫（P1-front 第一批 · 2026-10-05 重写）。

【解决什么 —— 这是 P1-front 的"第一块地基"】
前端页面层的 `hubGet` 依赖 `pickObj/pickList` **猜**响应形态。要删掉这个启发式，
前置是"被猜的那些端点先以 `X-Envelope` 显式声明形态"。本文件把**第一批**（心跳）
的这条契约钉住，使它可以被机械复核，而不是靠"我改过了"。

【为什么先做心跳】它的消费面只有 `pages/hub/engine/heartbeat.tsx` 一个文件，
且该页注释里已写明真实形状（`{history, limit, offset, total}`），改写时无需语义猜测。

【2026-10-05 重写：为什么**不再** import app_server】
初版用 `import app_server` 取真实 app 的 test_client，理由是"只有真实 app 才证明得了
这条路由接上了 ok()"。但实测该导入本机就要 80–100s（本仓
`test_server_routes_registration_inventory.py` 的头注记录了同一数字），
而在 CI 的**覆盖率分片**（多 worker xdist + coverage）下**超过 pytest-timeout 的 300s 预算**
⇒ 四条用例全部 `Failed: Timeout (>300.0s)`，把 `全项目测试覆盖率 (Shard 4/6)` 拖红。
（这正是本仓反复记录的那类归因错误：**先读失败原因** —— 它是超时，不是断言失败。）

⇒ 改法：把"有没有接上"与"接上后长什么样"**拆成两件事分别证明**，
   两者都**不需要**导入 app_server：

| 要证明的 | 手段 | 代价 |
|---|---|---|
| 这几个视图真的会声明信封、载荷形状没变 | 把 `plugins.status.bp` 挂到**最小 Flask app** 上打真实请求 | `import plugins.status` 实测 **0.48s** |
| 这条蓝图在生产确实被装配（不是死代码） | 静态：`PLUGIN.blueprint` 非空 + app_server 走 register_blueprint 装配 | 毫秒级 |

【为什么保留了"用真实视图而不是单测信封模块"这条初衷】
本仓有前科：`ok()` 曾有 23 条单测而**全仓 0 个调用点** ——
"模块已实现 + 单测全绿"完全不足以证明"机制已生效"。
故本文件打的是**这三个视图函数**：若有人把 `ok()` 改回 `jsonify()`，这里会红。
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]

#: 本批已迁移到显式信封的心跳端点。
MIGRATED = ["/api/heartbeat", "/api/heartbeat/history", "/api/heartbeat/status"]


@pytest.fixture(scope="module")
def status_mod():
    """只导入 status 插件（实测 0.48s）—— **绝不**导入 app_server（80–100s）。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("plugins.status")


@pytest.fixture(scope="module")
def client(status_mod):
    """把该插件的蓝图挂到最小 Flask app 上，并装上**真实**错误处理器。"""
    from flask import Flask
    from agent.api_envelope import install_error_handlers

    app = Flask(__name__)
    app.register_blueprint(status_mod.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


class Test心跳端点已声明信封:
    def test_成功响应带_X_Envelope_头(self, client):
        """**本批的核心断言**：成功路径必须带信封版本头。

        【本条失败有两个方向，消息里都要写到】
          · 没有头 ⇒ 该端点没接上 `ok()`（迁移被回退，或改错了文件）；
          · 不是 v2 ⇒ 信封版本变了，前端解析要跟着改。
        """
        missing, wrong = [], []
        for ep in MIGRATED:
            resp = client.get(ep)
            got = resp.headers.get("X-Envelope")
            if got is None:
                missing.append(ep + " (HTTP " + str(resp.status_code) + ")")
            elif got != "v2":
                wrong.append(ep + " -> " + str(got))
        assert not missing, (
            "以下端点响应上没有 X-Envelope 头 ⇒ 它们没有走 ok()/problem()。\n  "
            + "\n  ".join(missing)
            + "\n注意：这三个端点的**活体实现**在 plugins/status.py"
              "（agent/server_routes/routes_monitoring.py 里那份是 KNOWN_UNREGISTERED 死代码，"
              "改它不会生效）。"
        )
        assert not wrong, "信封版本不是 v2 ⇒ 前端解析契约要同步更新。实测：" + repr(wrong)

    def test_心跳体仍是既有的业务键(self, client):
        """迁移信封**不得**改变业务载荷（形状不变，只是被装进 data）。"""
        body = client.get("/api/heartbeat").get_json()
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

        【为什么要单独钉】该端点返回 `{history,total,limit,offset}`，前端按 `r["history"]` 取。
        装了信封后若只把 `history` 放进 data、把分页字段丢到 envelope 层，
        取分页的地方会**静默拿到 undefined**（不报错、只是少一页）。
        """
        body = client.get("/api/heartbeat/history?limit=5").get_json()
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


class Test生产确实挂上了这个蓝图:
    """上面那组打在**最小 app** 上 —— 它证明不了"生产也挂了这条蓝图"。

    【为什么必须补】本仓有过"模块写了路由但没接线 ⇒ 线上 404"的先例
    （见 `test_server_routes_registration_inventory.py` 的 KNOWN_UNREGISTERED）。
    这里用**静态 + 蓝图规则集**证明接线存在，避免为真实性去导入 80–100s 的 app_server。
    """

    def test_插件带蓝图(self, status_mod):
        plugin = getattr(status_mod, "PLUGIN", None)
        assert plugin is not None, "plugins/status.py 没有 PLUGIN —— 插件装配器不会挂它的路由"
        assert getattr(plugin, "blueprint", None) is not None, (
            "PLUGIN.blueprint 为空 ⇒ 该插件的路由不会被注册到任何 app（线上 404）"
        )
        assert plugin.blueprint is status_mod.bp, "PLUGIN.blueprint 与模块级 bp 不是同一个对象"

    def test_蓝图含有本批三个端点(self, status_mod):
        # Blueprint 在 register_blueprint 之前不持有规则列表，故用最小 app 取真实规则集。
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(status_mod.bp)
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        for ep in MIGRATED:
            assert ep in rules, (
                "端点「" + ep + "」不在该蓝图的规则集里 —— 路由被删/改名了。"
                "实测规则：" + repr(sorted(rules))[:300]
            )

    def test_app_server_仍走蓝图装配(self):
        """生产入口确实会装配蓝图（而不是只 import 不使用）。"""
        src = (ROOT / "app_server.py").read_text(encoding="utf-8")
        assert re.search(r"register_blueprint\s*\(", src), (
            "app_server.py 里找不到 register_blueprint —— 装配路径可能已改变，"
            "本文件关于'生产会挂上该蓝图'的推断需要重新核对。"
        )
        assert "plugins" in src, "app_server.py 里看不到 plugins 装配入口"
