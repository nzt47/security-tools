"""插件 manifest 路由**派生**守卫（2026-10-03 · 阶段 4 / R4 · 审计 H-1/H-6，指标 K2）。

【解决什么】plugins/*.py 里原本每个插件都手写一份 routes=[...]（人工补齐到 199 条），
与 blueprint 上真实的 @bp.route 构成**两个事实源**。审计实测：**11 处**
「插件真实注册了路由，但 manifest.routes 未声明」——前端插件面板少显示 11 条路由，
而没有任何东西会变红。这正是方案第 13 节的预言（"手工维护的必然漂移"）。

【怎么做】不是"让两份清单保持同步"，而是**只留一份**：
  · Plugin.routes 字段**删除**（误传即 TypeError）；
  · manifest().routes 由 plugin_routes() 从 **app.url_map** 派生；
  · app_server 在蓝图装配完成后 bind_app(app)（无请求上下文的路径也能派生）；
  · 静态 CI 改为**防回归**判定（不许再出现手写 routes）；
  · 运行期启动自检 audit_plugin_manifest() 守**派生完整性**
    （带蓝图的插件必须至少派生出 1 条路由 —— 派生为空与"本来没路由"在数据上无法区分，
     只能靠点名日志）。

【本文件的四个锚点（缺一个守卫就会退化成空壳）】
  ① 派生确实来自 url_map（改了蓝图的 route 装饰器，派生结果跟着变）；
  ② 字段确实删了（传 routes= 必须当场报错，而不是被静默忽略）；
  ③ 防回归（AST：plugins/*.py 不得再出现手写 routes=）；
  ④ 与 CI 判据同源（contract_diff.collect_manifest_declared() 必须返回空）。
"""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest
from flask import Blueprint, Flask

from plugins import plugin_api
from plugins.plugin_api import Plugin, bind_app, manifest, plugin_routes, unbind_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated():
    """隔离全局状态：注册表 + 绑定的 app。"""
    saved = list(plugin_api._REGISTRY)
    plugin_api._REGISTRY.clear()
    unbind_app()
    yield
    plugin_api._REGISTRY[:] = saved
    unbind_app()


def _make_app_with_bp(name, *routes):
    bp = Blueprint(name, __name__)

    def _view():
        return "ok"

    for i, route in enumerate(routes):
        bp.add_url_rule(route, endpoint=name + "_v" + str(i), view_func=_view)
    app = Flask("derive_" + name)
    app.register_blueprint(bp)
    return app, bp


class Test派生来自_url_map:
    def test_按蓝图派生且只含本插件的路由(self):
        app1, bp1 = _make_app_with_bp("alpha", "/api/alpha/one", "/api/alpha/two")
        _app2, bp2 = _make_app_with_bp("beta", "/api/beta/one")
        app1.register_blueprint(bp2)
        plugin_api.register_plugin(Plugin(name="alpha", version="1", blueprint=bp1))
        plugin_api.register_plugin(Plugin(name="beta", version="1", blueprint=bp2))

        entries = {p["name"]: p for p in manifest(app1)["plugins"]}
        assert entries["alpha"]["routes"] == ["/api/alpha/one", "/api/alpha/two"]
        assert entries["beta"]["routes"] == ["/api/beta/one"]
        assert entries["alpha"]["routes_source"] == "url_map"

    def test_派生结果跟着蓝图走而不是跟着清单走(self):
        """**这是本机制的核心**：manifest 的 routes 完全由 blueprint 上真实注册的规则决定。

        【为什么这样构造】Flask 不允许在 blueprint 已注册后再 add_url_rule
        （实测报 "The setup method 'add_url_rule' can no longer be called ...
        It has already been registered at least once"）—— 首版本用例就是这么做而红的。
        故改为：同一个插件名下换一个**规则不同**的蓝图，派生结果必须随之改变。
        若 routes 还是那份手写/缓存清单，这里必然不变。
        """
        app1, bp1 = _make_app_with_bp("gamma", "/api/gamma/one")
        plugin_api.register_plugin(Plugin(name="gamma", version="1", blueprint=bp1))
        assert manifest(app1)["plugins"][0]["routes"] == ["/api/gamma/one"]

        # 换一个"多注册了一条路由"的蓝图（同名插件，模拟代码演进而清单没跟上）
        plugin_api._REGISTRY.clear()
        app2, bp2 = _make_app_with_bp("gamma", "/api/gamma/one", "/api/gamma/two")
        plugin_api.register_plugin(Plugin(name="gamma", version="1", blueprint=bp2))
        assert manifest(app2)["plugins"][0]["routes"] == ["/api/gamma/one", "/api/gamma/two"], (
            "蓝图多了一条路由而派生结果没变 —— 说明 routes 又回到手写/缓存了"
        )

    def test_无蓝图的插件派生为空且不报错(self):
        app, _bp = _make_app_with_bp("delta", "/api/delta")
        plugin_api.register_plugin(Plugin(name="pure-declaration", version="1"))
        entry = next(p for p in manifest(app)["plugins"] if p["name"] == "pure-declaration")
        assert entry["routes"] == []
        assert entry["routes_source"] == "url_map"


class Test无上下文:
    def test_无绑定无上下文时为空且标注不可用(self):
        app, bp = _make_app_with_bp("eps", "/api/eps")
        plugin_api.register_plugin(Plugin(name="eps", version="1", blueprint=bp))
        entry = manifest()["plugins"][0]
        assert entry["routes"] == []
        assert entry["routes_source"] == "unavailable", (
            "派生不可用时必须显式标注 —— 否则「routes 为空」会被误读成"
            "「这个插件本来就没有路由」（本仓反复出现的「看起来正常、实际没数据」）"
        )

    def test_绑定后无上下文也能派生(self):
        """reload 路径（POST /api/plugins/reload → loader.refresh_manifest）没有请求上下文。"""
        app, bp = _make_app_with_bp("zeta", "/api/zeta")
        plugin_api.register_plugin(Plugin(name="zeta", version="1", blueprint=bp))
        bind_app(app)
        entry = manifest()["plugins"][0]
        assert entry["routes"] == ["/api/zeta"]
        assert entry["routes_source"] == "url_map"

    def test_应用上下文即可派生(self):
        app, bp = _make_app_with_bp("eta", "/api/eta")
        plugin_api.register_plugin(Plugin(name="eta", version="1", blueprint=bp))
        with app.app_context():
            entry = manifest()["plugins"][0]
        assert entry["routes"] == ["/api/eta"]


class Test字段已删除:
    def test_Plugin_不再有_routes_字段(self):
        names = {f.name for f in dataclasses.fields(Plugin)}
        assert "routes" not in names, (
            "Plugin.routes 又回来了 —— manifest 的 routes 必须只有一个来源（app.url_map）"
        )

    def test_传_routes_当场报错(self):
        """**静默忽略是最坏的选择**：那样插件的作者会以为自己的声明生效了。"""
        with pytest.raises(TypeError):
            Plugin(name="x", version="1", routes=["/api/x"])  # type: ignore[call-arg]

    def test_plugin_routes_对无效输入安全(self):
        assert plugin_routes(Plugin(name="n", version="1"), None) == []
        assert plugin_routes(Plugin(name="n", version="1"), Flask("empty")) == []


class Test防回归:
    def test_plugins_下不得再出现手写_routes(self):
        """AST 定位 `Plugin(..., routes=...)` —— 文本扫描会被注释/docstring 骗到。"""
        offenders = []
        for path in sorted((ROOT / "plugins").glob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "Plugin"):
                    for kw in node.keywords:
                        if kw.arg == "routes":
                            offenders.append(path.name + ":" + str(kw.lineno))
        assert not offenders, (
            "以下位置又给 Plugin 传了 routes=（第二个事实源复活）：" + str(offenders)
            + "。manifest 的 routes 由 app.url_map 派生，请删除该关键字。"
        )

    def test_与_CI_判据同源(self):
        """contract_diff 的新判据 collect_manifest_declared() 必须解析不出任何东西。

        它与 app_server 的启动自检是**两把尺子**：静态这把守"不许再加回来"，
        运行期那把守"派生必须完整"。这里钉住静态这把真的生效。
        """
        path = ROOT / "scripts" / "audit" / "contract_diff.py"
        spec = importlib.util.spec_from_file_location("cd_for_manifest", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["cd_for_manifest"] = mod
        spec.loader.exec_module(mod)
        declared = mod.collect_manifest_declared()
        assert declared == {}, (
            "contract_diff 仍能静态解析到手写 routes 声明：" + str(declared)
            + " —— 说明防回归判据面对的是旧状态"
        )


class Test启动自检确实接线:
    SRC = ROOT / "app_server.py"

    def test_绑定了_app(self):
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        assert "bind_app" in src, (
            "app_server 未绑定 app —— loader.refresh_manifest()（无请求上下文）"
            "派生不出 routes，表现为 reload 后插件面板路由全空且静默"
        )

    def test_有派生完整性自检且被调用(self):
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        assert "def audit_plugin_manifest" in src, "派生完整性自检被删了"
        assert "_plugin_manifest_audit = audit_plugin_manifest(app)" in src, (
            "自检定义了却没接线 —— 派生为空会静默通过（本仓「脚本无消费方」的同类形态）"
        )

    def test_自检失败不阻断启动(self):
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        idx = src.index("def audit_plugin_manifest")
        window = src[idx: idx + 2600]
        assert "except Exception" in window, "自检未做异常保护，会把服务挡在启动之外"


class Test真实插件:
    def test_status_插件派生非空(self):
        """用一个真实插件做端到端确认（不是只有合成夹具能过）。"""
        import plugins.status  # noqa: F401

        app = Flask("real_status")
        app.register_blueprint(plugins.status.PLUGIN.blueprint)
        derived = plugin_routes(plugins.status.PLUGIN, app)
        assert derived, "真实 status 插件派生到 0 条路由 —— 派生逻辑或蓝图装配有问题"
        assert "/api/status/config" in derived
