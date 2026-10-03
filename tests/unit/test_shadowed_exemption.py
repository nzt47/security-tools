"""影子豁免守卫（安全·2026-10-03）—— 与 H-5 守卫互补的另一半。

【解决什么】agent/server_auth.py 已有的 find_allowed_write_endpoints 查的是
「豁免清单**多**放开了本该受保护的写端点」（安全洞，审计 H-5）。
本文件守的是**互补的另一半**：「豁免条目**什么也没放开**，却宣称放开了」。

两套策略（全局闸门豁免清单 / 逐路由令牌装饰器）同时命中一条路径时，生效的是
**装饰器** —— 它在视图内部执行，闸门放不放行都拦不住。于是该豁免条目
**不产生任何效果**。

【为什么必须把它变成机械判定 —— 本次是实证，不是假想】
2026-10-03 的续作任务书把 \`/api/diagnostics/metrics\` 描述为
「为了 tokenless 页面 \`/dashboard\` 而保留的最后一个只读豁免」，并据此规划
「收敛该页即可摘掉最后一个豁免」。**该描述与事实不符**：
  · 静态：agent/server_routes/routes_logging.py:828 有 \`@require_token\`；
  · 活体：enforce_all 档下 \`curl\` 无令牌 \`GET\` 该端点 ⇒ **401**（本机实测）。
⇒ 该项豁免被遮蔽，页面**从来没能** tokenless 消费它。
错误结论的代价不是"少做一件事"，而是**规划建立在假前提上**：把"摘掉一个无效条目"
当成"关掉一个开放面"，从而低估剩余开放面、并高估那次收敛的安全收益。

【本文件的三个锚点（缺一个守卫就会退化成空壳）】
  ① 纯函数行为：命中判据、通配符、空集；
  ② **元守卫**：每个真实鉴权装饰器都必须打标记 —— 否则检测器静默失明；
     fail-open 兜底桩则**不得**打标记（打标会把"没有保护"说成"豁免无效"）。
  ③ 分辨力：正锚（已知被打标者必须检出）+ 反锚（未打标者必须排除）。

【为什么不导入 app_server】app_server 导入期会构造引擎并注册 Prometheus 计数器
（同进程二次导入直接 ValueError），实测 50s+。故本文件只测纯函数与可独立导入的
装饰器；真实 url_map 那一半由 app_server.audit_shadowed_exemptions() 在启动期做。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agent.server_auth import (
    REQUIRES_TOKEN_ATTR,
    find_shadowed_exemptions,
    is_token_guarded,
    path_is_allowlisted,
)

ROOT = Path(__file__).resolve().parents[2]

#: 规则夹具 —— 模拟 app.url_map.iter_rules() 的 (rule, methods) 形状。
REAL_WORLD = [
    ("/api/health", ("GET", "HEAD", "OPTIONS")),
    ("/api/diagnostics/metrics", ("GET", "HEAD", "OPTIONS")),
    ("/api/observability/alerts", ("GET", "POST", "OPTIONS")),
    ("/chat", ("GET", "HEAD", "OPTIONS")),
]


def _guarded(*paths):
    """只挑出"视图函数带令牌装饰器"的规则 —— 调用方的职责，与启动自检一致。"""
    return [(p, m) for p, m in REAL_WORLD if p in paths]


class TestFindShadowedExemptions:
    def test_检出影子豁免(self):
        """本机实际形态：豁免项 /api/diagnostics/metrics 带 @require_token。"""
        hits = find_shadowed_exemptions(
            ["/api/health", "/api/diagnostics/metrics"], _guarded("/api/diagnostics/metrics"))
        assert hits == ["/api/diagnostics/metrics (GET)"], hits

    def test_未受保护的豁免不算影子(self):
        """页面/探针豁免（无装饰器）是**真实生效**的，不得误报。"""
        hits = find_shadowed_exemptions(
            ["/api/health", "/chat"], _guarded("/api/diagnostics/metrics"))
        assert hits == [], hits

    def test_不在豁免清单里的受保护路由不算影子(self):
        """受装饰器保护但**没被豁免**的路由是常态（绝大多数路由都如此）。"""
        hits = find_shadowed_exemptions([], _guarded("/api/diagnostics/metrics"))
        assert hits == [], hits

    def test_只报_HEAD_OPTIONS_时退化为裸路径(self):
        """Flask 自动补的 HEAD/OPTIONS 不参与展示（无信息量）。"""
        hits = find_shadowed_exemptions(
            ["/api/x"], [("/api/x", ("HEAD", "OPTIONS"))])
        assert hits == ["/api/x"], hits

    def test_通配符豁免同样适用(self):
        """前缀豁免把一片路径放开；其中受装饰器保护的那些同样是影子条目。

        【为什么单列】H-5 的第一次事故正是前缀豁免（"/api/health" 曾 startswith 匹配），
        故通配形态必须与精确形态走同一判定。
        """
        hits = find_shadowed_exemptions(
            ["/api/diagnostics/*"], _guarded("/api/diagnostics/metrics"))
        assert hits == ["/api/diagnostics/metrics (GET)"], hits

    def test_空输入返回空(self):
        assert find_shadowed_exemptions([], []) == []
        assert find_shadowed_exemptions(["/x"], []) == []

    def test_结果按路径排序且去重语义稳定(self):
        hits = find_shadowed_exemptions(
            ["/chat", "/api/health"], [("/chat", ("GET",)), ("/api/health", ("GET",))])
        assert hits == sorted(hits)

    def test_与写端点检出互不干扰(self):
        """两面判据不同：本函数不关心方法是否变更型。

        /api/diagnostics/metrics 是 **GET** —— 正是这一点让它躲过了
        find_allowed_write_endpoints（那个只看 POST/PUT/DELETE/PATCH），
        也正是任务书误判的成因。若本函数也按变更型过滤，就会漏掉它。
        """
        hits = find_shadowed_exemptions(
            ["/api/diagnostics/metrics"], _guarded("/api/diagnostics/metrics"))
        assert hits, "只读端点的影子豁免必须被检出（任务书误判正是从这里来的）"


class TestIsTokenGuarded:
    def test_默认未打标(self):
        def bare():
            pass
        assert is_token_guarded(bare) is False

    def test_打标后为真(self):
        def view():
            pass
        setattr(view, REQUIRES_TOKEN_ATTR, True)
        assert is_token_guarded(view) is True

    def test_标记随_functools_wraps_向上传递(self):
        """外层再套一层 wraps 装饰器（如 @log_request）后标记仍在。

        【为什么必须验】路由的装饰器顺序有两种写法，两种都出现过：
            @app.route(...)          @app.route(...)
            @require_token           @log_request(...)
            @log_request(...)        @require_token
            def v(): ...             def v(): ...
        若标记只在某一序下存活，检测器就会按写法对错而时灵时不灵。
        """
        import functools

        def view():
            pass
        setattr(view, REQUIRES_TOKEN_ATTR, True)

        def outer(f):
            @functools.wraps(f)
            def w(*a, **k):
                return f(*a, **k)
            return w

        assert is_token_guarded(outer(view)) is True


class Test元守卫_每个真实鉴权装饰器都必须打标记:
    """检测器的**输入**是标记；打标记这件事必须有机械保证。

    【为什么单列一类】本仓已有两次同类事故（见 test_auth_coverage_baseline.py 的
    GUARD_MARKERS 注释）：新增鉴权装饰器后忘了登记，守卫立刻失真 ——
    要么把已受保护的端点误报为裸奔，要么（更坏）对它们完全失明。
    这里守的是同一个复发性缺陷，只是换了一个"登记处"。
    """

    def test_server_auth_require_token_打标(self):
        from agent.server_auth import require_token

        def view():
            pass
        assert is_token_guarded(require_token(view)) is True

    def test_plugin_api_require_auth_打标(self):
        """插件统一装饰器是**延迟**包装，wraps 传播链在导入期不存在。

        它必须自己打标；否则 plugins/ 整片对检测器失明。
        """
        from plugins.plugin_api import require_auth

        def view():
            pass
        assert is_token_guarded(require_auth(view)) is True

    def test_admin_api_require_admin_打标(self):
        from plugins.admin_api import _require_admin

        def view():
            pass
        assert is_token_guarded(_require_admin(view)) is True

    def test_app_server_require_token_打标(self):
        """app_server 的历史副本无法独立导入（导入期构造引擎），故读源码断言。

        【为什么仍然要守】它是**另一份实现**（agent/server_auth.py 那份是规范实现）。
        两份都要打标 —— 漏一份，走该副本的路由就对检测器失明。
        断言同时钉住"该函数必须存在"，避免它被删掉后本用例静默变成空断言。
        """
        src = (ROOT / "app_server.py").read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        found = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "require_token":
                found = node
                break
        assert found is not None, "app_server.py 里找不到 require_token —— 锚点失效，请迁移本用例"
        body = ast.get_source_segment(src, found) or ""
        assert "REQUIRES_TOKEN_ATTR" in body, (
            "app_server.py 的 require_token 未设置 "
            + REQUIRES_TOKEN_ATTR
            + " 标记 ⇒ 走该副本的路由对 find_shadowed_exemptions 失明"
        )

    def test_fail_open_兜底桩不得打标(self):
        """routes_capabilities.py 的兜底桩是 fail-open（无校验），不得声称受保护。

        【为什么这条反向断言同样关键】打标会把"这条路由其实没有保护"说成
        "它受保护、只是豁免无效"—— 正好把结论说反，比不检更坏。
        """
        src = (ROOT / "agent" / "server_routes" / "routes_capabilities.py").read_text(
            encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        stub = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_require_token":
                stub = node
                break
        assert stub is not None, (
            "routes_capabilities.py 里找不到 fail-open 兜底桩 _require_token —— "
            "锚点失效，请迁移本用例"
        )
        body = ast.get_source_segment(src, stub) or ""
        assert "REQUIRES_TOKEN_ATTR" not in body, (
            "fail-open 兜底桩不得打 " + REQUIRES_TOKEN_ATTR + " 标记（它没有校验任何令牌）"
        )


class Test分辨力:
    """证明判定不是恒空集 / 恒全集。"""

    def test_正锚_本机实际形态必须被检出(self):
        hits = find_shadowed_exemptions(
            ["/api/health", "/api/diagnostics/metrics", "/metrics"],
            _guarded("/api/diagnostics/metrics"))
        assert "/api/diagnostics/metrics (GET)" in hits

    def test_反锚_真实生效的页面豁免必须被排除(self):
        hits = find_shadowed_exemptions(
            ["/chat", "/", "/static/*"], _guarded("/api/diagnostics/metrics"))
        assert hits == []

    def test_豁免判定与server_auth同源(self):
        """本文件依赖 path_is_allowlisted 的语义；钉住它以免口径漂移。"""
        assert path_is_allowlisted("/api/diagnostics/metrics", ["/api/diagnostics/metrics"])
        assert not path_is_allowlisted("/api/diagnostics/metrics/x", ["/api/diagnostics/metrics"])
        assert path_is_allowlisted("/api/diagnostics/x", ["/api/diagnostics/*"])
