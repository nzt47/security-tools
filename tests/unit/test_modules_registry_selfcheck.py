"""模块注册表自校验守卫（2026-10-03 · 审计 H-2 的机制化修复 / 成功指标 K1）。

【解决什么】agent/modules_registry.py 是一份**手写声明**：6 域 32 节点 + 23 条动作，
每个节点用 status_source 声明"状态从哪取"、每条动作声明"打哪个 method+url"。
声明与事实一旦偏离，**不会有任何请求失败** —— 只会让拓扑图说谎：
  · status_source 指向不存在的端点 ⇒ 该节点状态永远显示"未知"；
  · ACTION_ROUTES 的 url/method 不对 ⇒ 前端渲染出的按钮点了 404/405；
  · node.path 指向不存在的文件 ⇒ 详情面板跳转到空处。
审计 H-2 就是第三种形态（service.gateway 声明了一个不存在的 api_gateway_flask.py），
而它被发现靠的是**三个月后的一次人工审计**，不是任何机制。

【两把尺子，缺一不完整】
  · 静态（CI · scripts/audit/contract_diff.py）：AST/正则扫 3 个注册面，
    已有的三类判定 registry_status_source_missing / registry_path_missing /
    registry_action_url_missing 都在那里；
  · 运行期（本文件 + app_server.audit_modules_registry）：用**真实 url_map**，
    任何静态看不到的注册方式（add_url_rule、运行期动态注册）都无处可藏。
本文件同时守"纯函数判据"与"今天的真实数据确实零漂移"（后者是 K1 的锚点）。

【纪律】新增节点/动作后请跑一次
    python scripts/audit/contract_diff.py
本文件会红在 test_当前真实路由集下零漂移 上，提示你去改正声明。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from agent.modules_registry import (  # noqa: E402
    ACTION_ROUTES,
    DOMAINS,
    declared_http_endpoints,
    validate_against_url_map,
)


def rules(*items):
    """模拟 app.url_map.iter_rules() 的 (rule, methods) 迭代器。"""
    return list(items)


FIXTURE = rules(
    ("/api/sensors", ("GET", "HEAD", "OPTIONS")),
    ("/api/panorama", ("GET", "HEAD", "OPTIONS")),
    ("/api/knowledge/cards", ("GET", "HEAD", "OPTIONS")),
    ("/api/knowledge/graph", ("GET", "HEAD", "OPTIONS")),
    ("/api/tools/toggle", ("POST", "OPTIONS")),
    ("/api/permission/emergency", ("POST", "OPTIONS")),
)


class TestDeclaredEndpoints:
    def test_覆盖节点与动作两类来源(self):
        items = declared_http_endpoints()
        sources = {i["source"].split(":")[0] for i in items}
        assert sources == {"node", "action"}, sources

    def test_只收_api_前缀的_status_source(self):
        """config: 类的 status_source 不是 HTTP 端点，不得进入校验（否则恒假警报）。"""
        for item in declared_http_endpoints():
            assert not item["path"].startswith("config:"), item

    def test_动作方法被规范化成大写(self):
        for item in declared_http_endpoints():
            if item["source"].startswith("action:"):
                assert item["method"] == item["method"].upper()

    def test_条数与注册表规模一致(self):
        items = declared_http_endpoints()
        n_nodes_api = sum(
            1 for d in DOMAINS for n in d.nodes
            if (n.status_source or "").strip().startswith("api:")
        )
        assert len(items) == n_nodes_api + len(ACTION_ROUTES)


class TestValidate:
    def test_全部命中时返回空(self):
        """用只含被测声明的夹具：把 53 条真实声明过滤到夹具覆盖的那几条。"""
        covered = {"api:/api/sensors", "api:/api/panorama"}
        items = [i for i in declared_http_endpoints() if "api:" + i["path"] in covered]
        assert items, "夹具前提失效：找不到 /api/sensors 或 /api/panorama 的声明"
        # 直接对全量跑会报其余 51 条，故这里只验证"覆盖到的那几条确实命中"
        misses = validate_against_url_map(FIXTURE)
        for item in items:
            assert item["source"] + " " + item["method"] + " " + item["path"] not in misses

    def test_路径不存在即报(self):
        misses = validate_against_url_map(rules(("/api/other", ("GET",))))
        assert any("/api/sensors" in m for m in misses), misses

    def test_方法不符即报(self):
        """路径对、方法错同样要报 —— 前端点按钮会拿 405。"""
        misses = validate_against_url_map(rules(("/api/tools/toggle", ("GET",))))
        assert any("action:toggle_tool" in m for m in misses), misses

    def test_通配声明命中其下任一规则(self):
        """节点用 /api/knowledge/* 表达"这一片都归我"是合法声明，不得误报。"""
        misses = validate_against_url_map(rules(("/api/knowledge/cards", ("GET",))))
        assert not any("/api/knowledge/*" in m for m in misses), misses

    def test_通配声明在其下无规则时报(self):
        misses = validate_against_url_map(rules(("/api/other", ("GET",))))
        assert any("/api/knowledge/*" in m for m in misses), misses

    def test_空规则集下全部报(self):
        """证明判定不是恒空集（否则守卫是个空壳）。"""
        assert len(validate_against_url_map([])) == len(declared_http_endpoints())

    def test_返回已排序(self):
        misses = validate_against_url_map(rules(("/api/other", ("GET",))))
        assert misses == sorted(misses)


@pytest.fixture(scope="module")
def cd():
    """加载契约对拍工具（它复用同一套静态路由扫描，不重复实现）。"""
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("contract_diff_for_registry", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["contract_diff_for_registry"] = mod
    spec.loader.exec_module(mod)
    return mod


class Test当前真实路由集:
    """K1 锚点：今天的注册表必须**零漂移**（这是"100% 命中率"的可执行形式）。"""

    def test_当前真实路由集下零漂移(self, cd):
        routes = cd.collect_routes_static()
        assert routes, "静态路由扫描返回空集 —— 前提失效，本用例会变成空壳"
        rules = [(v["path"], v["methods"]) for v in routes.values()]
        misses = validate_against_url_map(rules)
        assert not misses, (
            "modules_registry 有 " + str(len(misses)) + " 条声明未命中真实路由：\n  "
            + "\n  ".join(misses)
            + "\n修法：改正声明，或补上缺失的路由；然后重跑 scripts/audit/contract_diff.py。"
        )

    def test_扫描规模足以支撑判定(self, cd):
        """静态扫描必须真的扫到了量级正确的路由集（否则"零漂移"可能是扫了个空集）。"""
        routes = cd.collect_routes_static()
        assert len(routes) > 300, "静态扫描只找到 " + str(len(routes)) + " 条路由，量级不对"

class Test启动自检确实接线:
    """守卫存在 ≠ 守卫在跑（本仓记录过"脚本没有任何消费方"的形态）。"""

    SRC = ROOT / "app_server.py"

    def test_app_server_调用了注册表自检(self):
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        assert "def audit_modules_registry" in src, "自检函数被删了"
        assert "_modules_registry_audit = audit_modules_registry(app)" in src, (
            "自检函数定义了却没有被调用 —— K1 会退化成一次性人工审计"
        )

    def test_自检失败不阻断启动(self):
        """与其他降级装配一致：声明不准不该让服务起不来（它只影响拓扑图的可信度）。"""
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        idx = src.index("def audit_modules_registry")
        window = src[idx: idx + 2600]
        assert "except Exception" in window, (
            "自检未做异常保护 —— 它一旦抛异常会把服务挡在启动之外，"
            "而它守的只是「声明准不准」"
        )


class Test状态源接线覆盖:
    """守卫：不允许存在"声明了 api: 状态源、却永远不会被采集"的节点（会永久离线）。

    【本用例的由来】2026-10-16 实测：拓扑页 32 个节点里 17 个长期显示"离线（无数据）"，
    其中 14 个对应的路由其实是 200 —— 它们只是没有专用 provider，而聚合器只认已注册的
    采集器。声明与接线一旦再次脱节，本用例会红在"未覆盖清单"上。
    """

    def test_所有api状态源都有provider覆盖(self):
        import agent.modules_api as ma

        declared = {
            n.status_source.strip()[len("api:"):]
            for d in DOMAINS for n in d.nodes
            if (n.status_source or "").strip().startswith("api:")
        }
        covered = (
            set(ma.INJECTED_STATUS_SOURCES)
            | set(ma.CLIENT_STATUS_SOURCES)
            | set(ma._default_providers)
        )
        missing = sorted(declared - covered)
        assert not missing, (
            "以下 api: 状态源既无专用 provider 也未登记 client 兜底，节点会永远显示离线："
            + repr(missing)
        )

    def test_每个config状态源都能在config_yaml解析(self):
        import agent.modules_api as ma

        bad = []
        for d in DOMAINS:
            for n in d.nodes:
                src = (n.status_source or "").strip()
                if src.startswith("config:"):
                    if ma._read_config_path(src[len("config:"):]) is None:
                        bad.append(n.module_id + " -> " + src)
        assert not bad, (
            "config: 状态源在 config.yaml 里解析不到（节点会永久离线，且没有任何请求失败）："
            + repr(bad)
        )


class TestClientStatusProvider:
    """运行期行为：client 兜底 provider 对 200 判"运行中"、对非 200 仍判离线。"""

    @staticmethod
    def _app_with(routes):
        from flask import Flask

        import agent.modules_api as ma
        app = Flask(__name__)
        app.register_blueprint(ma.modules_bp)
        for path, resp in routes.items():
            def _view(r=resp):
                return r()
            app.add_url_rule(path, "ep_" + path.replace("/", "_"), _view, methods=["GET"])
        return app

    @staticmethod
    def _node(data, module_id):
        return next(n for d in data["domains"] for n in d["nodes"] if n["module_id"] == module_id)

    def test_200路由采集为非离线(self, monkeypatch):
        from flask import jsonify
        import agent.modules_api as ma

        monkeypatch.setattr(ma, "_api_token_provider", None)
        app = self._app_with({"/api/personality": lambda: jsonify({"ok": True})})
        ma.register_client_status_providers({"/api/personality": "/api/personality"})
        try:
            data = app.test_client().get("/api/modules/topology").get_json()["data"]
        finally:
            ma.reset_status_providers()
        node = self._node(data, "ops.personality")
        assert node["status"] == "healthy", node
        assert node["status_detail"] == "运行中", node

    def test_非200仍如实离线(self, monkeypatch):
        from flask import jsonify
        import agent.modules_api as ma

        monkeypatch.setattr(ma, "_api_token_provider", None)
        app = self._app_with({"/api/personality": lambda: (jsonify({"error": "down"}), 503)})
        ma.register_client_status_providers({"/api/personality": "/api/personality"})
        try:
            data = app.test_client().get("/api/modules/topology").get_json()["data"]
        finally:
            ma.reset_status_providers()
        node = self._node(data, "ops.personality")
        assert node["status"] == "offline", node
        assert node["status_detail"] == "无数据", node

