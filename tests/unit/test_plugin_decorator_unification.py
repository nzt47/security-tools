"""插件鉴权/日志装饰器**唯一实现**守卫（审计 M-40 收口 · 2026-10-03 续作）。

【解决什么】审计 M-40 实测：plugins/{chat,mcp_scheduler,safety,skills,status}.py 各自
抄了一份 _lazy_wrap + _require_token（共 5 份定义、64 处使用），改一处不会同步其余四处。
本仓已有同类前科（agent/logging_utils.py 与 utils/sensitive_data_filter.py 的两份掩码规则
「对齐而非共用」；以及 require_token 空装饰器 fail-open）。

2026-10-03 首轮（提交 b65e06a3）把 **鉴权** 那一半上收到 plugins/plugin_api；
同轮**刻意留下**了 plugins/status.py 的 _log_request（60 行独立重写），理由写的是
「含自己的字段拼装，属另一件事，不在此夹带」。本轮补完，且发现"留下"是有代价的：

    · 宿主实现（agent/server_auth.py:498）用 response[0].get_data(as_text=True)；
    · status.py 那份用 response[0].get_json() —— 响应不是 JSON 时会**抛异常**，
      而它挂在 5 条 @_log_request()（show_response 默认 True）的路由上，
      即"只读端点返回非 JSON 就被日志装饰器变成 500"。
    ⇒ 两份实现不是"等价"，而是"分叉"。这就是本文件要机械禁止的形态。

【纪律】**新增插件一律 from .plugin_api import log_request / require_auth**；
不得再在插件内定义同名装饰器或自建 _lazy_wrap。本文件以 AST 扫描机械保证。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = sorted((ROOT / "plugins").glob("*.py"))

#: 使用统一装饰器的插件（审计 M-40 点名的 5 个）。
SHARED_CONSUMERS = ["chat", "skills", "safety", "mcp_scheduler", "status"]

#: 禁止在插件内重新定义的符号（一旦复现，唯一实现就开始分叉）。
#: 注：_lazy_wrap **不在**此列 —— 它的正主就是 plugin_api.py 自己，
#: 由 test_lazy_wrap_只在_plugin_api_里 单独守（首版把它并进来，结果 plugin_api
#: 自己成了"违规者"，是一条自己打自己的假阳性）。
FORBIDDEN_LOCAL_DEFS = ("_require_token", "_log_request")


def _defined_here(tree: ast.Module):
    """返回本模块顶层定义的函数名集合（不含 import 进来的别名）。"""
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
    return out


class Test插件共用同一份装饰器:
    def test_鉴权装饰器是同一对象(self):
        from plugins import plugin_api

        for name in SHARED_CONSUMERS:
            mod = __import__("plugins." + name, fromlist=[name])
            assert getattr(mod, "_require_token") is plugin_api.require_auth, (
                name + "._require_token 不是 plugin_api.require_auth —— "
                "说明又出现了一份独立实现（审计 M-40 复发）"
            )

    def test_日志装饰器是同一对象(self):
        from plugins import plugin_api

        for name in SHARED_CONSUMERS:
            mod = __import__("plugins." + name, fromlist=[name])
            assert getattr(mod, "_log_request") is plugin_api.log_request, (
                name + "._log_request 不是 plugin_api.log_request —— "
                "审计 M-40 的日志那一半（status.py 曾单独保留 60 行重写）又分叉了"
            )

    def test_插件内不得再定义同名装饰器(self):
        """AST 扫描：plugins/*.py 顶层不得出现 _require_token / _log_request / _lazy_wrap。"""
        offenders = []
        for path in PLUGINS:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - 语法错误由别的守卫负责
                continue
            hit = _defined_here(tree) & set(FORBIDDEN_LOCAL_DEFS)
            if hit:
                offenders.append(path.name + " -> " + ", ".join(sorted(hit)))
        assert not offenders, (
            "以下插件重新定义了已上收的装饰器（应改为 "
            "from .plugin_api import log_request, require_auth）：\n  "
            + "\n  ".join(offenders)
        )

    def test_lazy_wrap_只在_plugin_api_里(self):
        """_lazy_wrap 是延迟包装的**唯一**实现；外面再有一份就必然分叉。"""
        holders = []
        for path in PLUGINS:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover
                continue
            if "_lazy_wrap" in _defined_here(tree):
                holders.append(path.name)
        assert holders == ["plugin_api.py"], (
            "_lazy_wrap 应只在 plugin_api.py 中定义，实际：" + str(holders)
        )


class Test统一装饰器本身可用:
    def test_plugin_api_导出两个装饰器(self):
        from plugins import plugin_api

        assert callable(plugin_api.require_auth)
        assert callable(plugin_api.log_request)

    def test_require_auth_返回可调用且已打标(self):
        """打标是"这条路由受令牌保护"的机械事实（见 tests/unit/test_shadowed_exemption.py）。"""
        from agent.server_auth import is_token_guarded
        from plugins import plugin_api

        def view():
            pass

        assert callable(plugin_api.require_auth(view))
        assert is_token_guarded(plugin_api.require_auth(view)) is True

    def test_log_request_接受宿主同名参数(self):
        """调用点写的是 @_log_request(show_response=False)，参数必须继续被接受。"""
        from plugins import plugin_api

        deco = plugin_api.log_request(show_response=False)
        assert callable(deco)

        def view():
            return "ok"

        assert callable(deco(view))
