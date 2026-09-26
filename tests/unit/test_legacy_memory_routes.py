# tests/unit/test_legacy_memory_routes.py
"""守门测试：legacy 记忆/向量端点必须存在于**真实入口** app_server 的 url_map 中。

为什么必须有这条测试（本仓两次同源事故）：
  1. `/api/agent-lines` 当年只在 `agent/server_routes/__init__.py::register_all_routes`
     登记，而该函数**没有调用方**（死代码）⇒ 生产从未注册，前端 404；但单测里手工
     `Flask(__name__)` + `register_routes(app, state)` 全部通过 ⇒ **测试绿、线上 404**。
  2. 本次同类：`agent/server_routes/routes_memory.py` 整模块从未被 `app_server.py`
     注册，其 22 条路径中 8 条生产 404；而 `app_server.py::legacy_ui` 仍把
     `templates/index.html` 挂在 `/legacy`，该页面加载 `static/js/sidebar/memory.js`，
     其中确实在调用这些端点。

因此本测试**只认真实入口**：`import app_server` 后枚举 `app.url_map`。
绝不手工造 Flask app —— 那种写法在端点缺失时依然全绿，正是当年漏网的原因。

同时它守住第二类 bug：**前端调了后端不存在的端点**（对 memory.js 里每个
`/api/...` fetch 路径做 url_map 归属校验）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    # 真实入口会加载向量模型/传感器（≈1min），放宽本模块超时（全局默认 120s）
    pytest.mark.timeout(900),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_JS = REPO_ROOT / "static" / "js" / "sidebar" / "memory.js"

# ── 本次补齐的 8 条 legacy 端点（路径 → 必须支持的方法）──
# 来源：已退役的 agent/server_routes/routes_memory.py（22 条声明中的这 8 条曾生产 404）
LEGACY_ENDPOINTS = {
    "/api/memory/review": {"GET", "POST"},
    "/api/vector/stats": {"GET"},
    "/api/vector/add": {"POST"},
    "/api/vector/batch_add": {"POST"},
    "/api/vector/item/<item_id>": {"GET"},
    "/api/vector/recent": {"GET"},
    "/api/vector/clear": {"DELETE"},
    "/api/knowledge/add": {"POST"},
}

# ── 插件已提供、不得重复注册的 14 条活体（回归护栏）──
ALREADY_LIVE_ENDPOINTS = {
    "/api/memory/overview",
    "/api/memory/manual",
    "/api/memory/compress",
    "/api/memory/<int:index>",
    "/api/memory/clear-summary",
    "/api/memory/summary",
    "/api/vector/search",
    "/api/memory/windows/events",
    "/api/memory/windows/stats",
    "/api/memory/windows/current",
    "/api/memory/windows/config",
    "/api/memory/windows/clear",
    "/api/window/consent",
    "/api/privacy/info",
}


def _rule_map(app) -> dict:
    """url_map → {规则: [(endpoint, 方法集), ...]}（HEAD/OPTIONS 为 Flask 自动添加，剔除）"""
    rules: dict = {}
    for rule in app.url_map.iter_rules():
        rules.setdefault(str(rule.rule), []).append(
            (rule.endpoint, set(rule.methods) - {"HEAD", "OPTIONS"})
        )
    return rules


@pytest.fixture(scope="module")
def real_app(tmp_path_factory):
    """真实入口 app_server 的 Flask app（数据落盘改指临时目录）。

    【为什么 chdir】`import app_server` 有重副作用：起调度线程、加载向量模型、
    并向 `./data/**`、`./logs/**` 落盘。本仓这些路径**都是相对 cwd 的**
    （无全局数据目录环境变量），故导入期间把 cwd 切到临时目录：导入期解析出的
    绝对路径全部指向 tmp，生产 `data/**` 不被触碰。导入完成后恢复 cwd。

    注：tests/conftest.py 的会话级隔离已覆盖审批/事件/审计四个路径；本夹具补的是
    app_server 自身（memory/planning/vector/session）的落盘。
    """
    from agent import tools as _tools

    workdir = tmp_path_factory.mktemp("legacy_routes_real_entry")
    prev_cwd = os.getcwd()
    # `import app_server` 会登记整套内建工具（实测 91 个）到**进程级**注册表；
    # 本夹具在模块结束时**整表还原**，别让导入副作用漏给同进程的其它测试文件
    # （实测：本文件排在 tests/unit/test_tool_count_consistency.py 之前时后者必红 2 条）。
    saved_registry = dict(_tools._registry)
    os.chdir(workdir)
    try:
        import app_server  # noqa: PLC0415  真实入口，绝不用 Flask(__name__) 手搓

        yield app_server.app
    finally:
        os.chdir(prev_cwd)
        _tools._registry.clear()
        _tools._registry.update(saved_registry)
        _tools._registry_version += 1


def test_legacy_endpoints_exist_in_real_entry(real_app):
    """8 条 legacy 端点必须都在真实入口的 url_map 里（当年 404 的正面防线）"""
    rules = _rule_map(real_app)

    missing = [p for p in LEGACY_ENDPOINTS if p not in rules]
    assert not missing, (
        "以下 legacy 端点缺失于真实入口 app_server 的 url_map（生产会 404）："
        f"{missing}；/legacy 页面仍在调用它们"
    )

    # 方法也必须对得上（路由在、方法不对同样是前端 404/405）
    for path, expected in LEGACY_ENDPOINTS.items():
        actual = set()
        for _endpoint, methods in rules[path]:
            actual |= methods
        assert expected <= actual, f"{path} 缺少方法 {expected - actual}（实际 {sorted(actual)}）"


def test_no_duplicate_registration_for_migrated_paths(real_app):
    """迁移路径必须**唯一**注册：本次是「补进插件」而非「整模块接线」的护栏。

    若有人改回 `routes_memory.register_routes(app, state)`，这 14 条已活路径会出现
    同一规则多条 endpoint（Flask 行为不可预期），本用例立刻失败。
    """
    rules = _rule_map(real_app)
    duplicated = {
        path: providers
        for path, providers in rules.items()
        if len(providers) > 1 and path in (set(LEGACY_ENDPOINTS) | ALREADY_LIVE_ENDPOINTS)
    }
    assert not duplicated, f"这些路径被重复注册：{duplicated}"

    for path in ALREADY_LIVE_ENDPOINTS:
        assert path in rules, f"既有活体 {path} 消失了（不该被本次改动波及）"


# ════════════════════════════════════════════════════════════
#  前端调用面校验：memory.js 里每个 /api fetch 路径都必须能命中 url_map
# ════════════════════════════════════════════════════════════

_FETCH_RE = re.compile(r"""fetch\(\s*['"`]([^'"`]+)['"`]""")


def _frontend_api_paths(js_path: Path) -> list[str]:
    """抽出前端 fetch('...') 里的 /api 路径（去查询串；保留运态拼接的前缀）"""
    source = js_path.read_text(encoding="utf-8")
    paths = []
    for raw in _FETCH_RE.findall(source):
        if not raw.startswith("/api/"):
            continue
        path = raw.split("?", 1)[0]
        if path not in paths:
            paths.append(path)
    return paths


def _resolvable(path: str, rules: dict) -> bool:
    """路径是否被 url_map 覆盖（动态拼接如 '/api/memory/' + index 按前缀放行）"""
    if path in rules:
        return True
    if path.endswith("/"):
        return any(rule.startswith(path) for rule in rules)
    return False


def test_memory_js_fetch_paths_all_exist(real_app):
    """memory.js 里每个 /api fetch 路径都必须命中真实 url_map（钉死"前端调了不存在的端点"）"""
    assert MEMORY_JS.is_file(), f"前端文件不存在：{MEMORY_JS}"
    rules = _rule_map(real_app)

    paths = _frontend_api_paths(MEMORY_JS)
    assert paths, "未从 memory.js 抽出任何 /api fetch 路径（正则或文件结构已变，测试需同步）"

    unresolved = [p for p in paths if not _resolvable(p, rules)]
    assert not unresolved, (
        f"memory.js 调用了 url_map 中不存在的端点（生产 404）：{unresolved}"
    )

    # 本次修复的 8 条必须全部由前端真实调用（防止修复对象漂移）
    called = set(paths)
    for legacy in ("/api/vector/stats", "/api/vector/recent", "/api/vector/add",
                   "/api/vector/batch_add", "/api/vector/clear", "/api/knowledge/add"):
        assert legacy in called, f"{legacy} 已不在 memory.js 调用面内，本测试的修复对象需复核"
