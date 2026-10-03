"""回归守卫（阶段 0 / R1）：契约对拍工具的匹配器不得退化。

【为什么值得单测】本工具的价值**完全**取决于"零假阳性"：一个会误报的门禁没人会看，
最终等于没有门禁（本仓已有先例：M-35 记录的多次"改动未触发 CI"事故）。
开发过程中实测到 4 类假阳性，每一类都在此锁死，防止后人改动匹配逻辑时回退：
  ① TS 模板字面量 ${...} 残留 "$"（曾产生 60+ 条假阳性）；
  ② Blueprint url_prefix 未解析（/api/modules/topology 被误判不存在）；
  ③ 模块级常量前缀 f"{PREFIX}/x" 未展开（/api/cp/tool-exemptions 被误判）；
  ④ 查询串模板 /api/x/stream${q}（q 是 "?a=b"，不是路径段）；
  ⑤ **docstring 里的用法示例被当成真路由** —— 2026-10-03 实测：给 plugins/plugin_api.py
     的 require_auth 写用法示例后，文本扫描把 /api/x 计成真实端点，routes_total 449→450。
"""
from __future__ import annotations

import ast

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cd():
    """从路径加载 scripts/audit/contract_diff.py（它不是包的一部分）。"""
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("contract_diff_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["contract_diff_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


ROUTES = {
    "/api/modules/topology": {"methods": ["GET"], "where": ["agent/modules_api.py:1"]},
    "/api/cp/tool-exemptions": {"methods": ["GET", "POST"], "where": ["x.py:1"]},
    "/api/skills-mgmt/assess/stream": {"methods": ["GET"], "where": ["y.py:1"]},
    "/api/schedules/<task_id>": {"methods": ["DELETE"], "where": ["p.py:1"]},
    "/api/schedules/<task_id>/pause": {"methods": ["POST"], "where": ["p.py:1"]},
    "/api/mcp/services/<string:service_id>": {"methods": ["DELETE"], "where": ["p.py:1"]},
}


class TestNorm:
    def test_模板字面量残留美元号被剥离(self, cd):
        assert cd._norm("/api/skills-mgmt/${item.id}/publish") == "/api/skills-mgmt/<>/publish"

    def test_flask_转换器归一(self, cd):
        assert cd._norm("/api/schedules/<task_id>") == "/api/schedules/<>"
        assert cd._norm("/api/mcp/services/<string:service_id>") == "/api/mcp/services/<>"

    def test_冒号风格归一(self, cd):
        assert cd._norm("/api/x/:id/y") == "/api/x/<>/y"


class TestRouteKnown:
    def test_精确命中(self, cd):
        assert cd._route_known("/api/modules/topology", ROUTES) is True

    def test_模板字面量动态段命中(self, cd):
        """②/①：/api/schedules/${id}/pause 应命中 /api/schedules/<task_id>/pause。"""
        assert cd._route_known("/api/schedules/${id}/pause", ROUTES) is True

    def test_查询串模板后缀容错(self, cd):
        """④：/api/skills-mgmt/assess/stream${q} 的 ${q} 是 ?a=b，不是路径段。"""
        assert cd._route_known("/api/skills-mgmt/assess/stream${q}", ROUTES) is True

    def test_子树声明命中(self, cd):
        assert cd._route_known("/api/modules/*", ROUTES) is True

    def test_真正缺失的端点必须判为未知(self, cd):
        """本工具实测检出的两个真实前端/后端不一致，不得被容错吃掉。"""
        assert cd._route_known("/api/schedules/${id}/delete", ROUTES) is False
        assert cd._route_known("/api/mcp/services/${id}/delete", ROUTES) is False

    def test_完全不同路径判为未知(self, cd):
        assert cd._route_known("/api/does-not-exist", ROUTES) is False


class TestStaticScan:
    def test_Blueprint前缀被解析(self, cd):
        """②：/api/modules/topology 由 url_prefix 注册，静态扫描必须能还原。"""
        routes = cd.collect_routes_static()
        assert "/api/modules/topology" in routes, "Blueprint url_prefix 解析回退"

    def test_常量前缀被展开(self, cd):
        """③：f"{PREFIX}/tool-exemptions" 形式必须能还原为完整路径。"""
        routes = cd.collect_routes_static()
        assert "/api/cp/tool-exemptions" in routes, "f-string 常量前缀展开回退"

    def test_嵌套副本被排除(self, cd):
        """M-26：security-tools/ 是未跟踪的完整副本，必须排除，否则产出双份契约。"""
        routes = cd.collect_routes_static()
        assert not any("security-tools" in w for r in routes.values() for w in r["where"])


class TestDocstringIsNotARoute:
    """(5) 类假阳性：源码文本里的用法示例不得被当作真实路由。"""

    def test_docstring_里的路由示例不算真路由(self, cd):
        # 用 chr(34)/chr(10) 拼装，避免在测试里嵌套三引号（本仓多处已因嵌套引号踩坑）
        src = (
            'def f():' + chr(10)
            + '    ' + chr(34) * 3 + '用法示例:' + chr(10)
            + chr(10)
            + '        @bp.route(' + chr(34) + '/api/x' + chr(34) + ', methods=[' + chr(34) + 'POST' + chr(34) + '])' + chr(10)
            + '    ' + chr(34) * 3 + chr(10)
            + '    return 1' + chr(10)
        )
        assert cd._real_route_decorator_lines(ast.parse(src)) == {}

    def test_真实装饰器仍被识别(self, cd):
        src = '@bp.route("/api/y", methods=["POST"])\n@require_auth\ndef g():\n    pass\n'
        assert list(cd._real_route_decorator_lines(ast.parse(src)).values()) == ["route"]

    def test_plugin_api_的用法示例未污染真实路由集(self, cd):
        routes = cd.collect_routes_static()
        assert "/api/x" not in routes, (
            "plugin_api.py 的 docstring 用法示例被当成了真实路由 —— 门禁会凭空造出端点。"
        )