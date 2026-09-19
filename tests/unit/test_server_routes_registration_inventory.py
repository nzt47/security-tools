"""路由注册清单守门：`server_routes/*` 里的 `register_routes` 必须"要么接线、要么显式登记"

## 为什么需要这个测试

本仓的路由注册有**两个**入口，而只有一个是活的：

1. `app_server.py` 里的显式 `from agent.server_routes.routes_X import register_routes` 块
   —— **真实位置**（`import app_server` 后枚举 `url_map` 得到的事实）；
2. `agent/server_routes/__init__.py::register_all_routes` —— **无调用方的死代码**。

`/api/agent-lines` 曾经只加进 (2)，线上就是 HTTP 404（`app_server.py:1014` 留了警告）。
更隐蔽的是另一种状态：模块**写了** `register_routes`、但既没进 (1)、也没人知道它没接线
（本次审计实测 13 个模块如此，其中 `routes_memory` 有 8 个端点在线上 404，
而 `/legacy` 页面的 `static/js/sidebar/memory.js` 正在调它们）。

本测试把这种**沉默状态**变成**显式清单**：
- 新增一个 `server_routes/*.py` 模块并写了 `register_routes`，
  ⇒ 要么在 `app_server.py` 里接线，要么加进下面的 `KNOWN_UNREGISTERED` 并写明原因；
- 清单条目一旦**过期**（模块被接线了 / 文件被删了）也会失败，逼着维护者同步，
  免得清单变成一张谎话表。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROUTES_DIR = PROJECT_ROOT / "agent" / "server_routes"
APP_SERVER = PROJECT_ROOT / "app_server.py"

# ══ 【D2 · 2026-09-19 显式超时预算】══
# 本模块的 `real_url_paths` fixture 会 `import app_server`（真实入口，见其 docstring），
# 这条导入实测 **80–100 秒**，是模块级夹具，**计时落在 pytest-timeout 的单测试预算里**：
#
#   实测（本机，2026-09-19，并行任务在跑）：
#     python -c "import app_server"                             ≈ 80.5s / 87.7s（两次）
#     coverage run --source=agent -c "import app_server"        ≈ 96.5s
#     其中 `import sentence_transformers`                        ≈ 19.9s
#     其中 `import transformers`（含 transformers/models 递归扫描）≈ 8.2s
#       —— `create_import_structure_from_path` 递归扫 993 个子目录 / 2256 个 .py
#     其中 `agent/orchestrator/lifecycle_manager.py:118` 的
#       `import sentence_transformers` 是**有意的**预导入（规避 Windows
#       0xC0000005 ACCESS_VIOLATION，见该处注释），**不能删**。
#
# Why 必须显式给预算、而不是靠全局 --timeout=120/300：
#   pytest.ini 的 `--timeout-method=thread` 在超时时走
#   `pytest_timeout.py:505 timeout_timer()` → `finally: os._exit(1)`
#   ⇒ **整个 pytest 进程被杀**，同批次排在后面的测试文件一个都不会执行
#     （TASK-03 实测：第 8 块在 73% 处被杀，该块 63 个文件里 14 个从未执行），
#     而且日志里没有结束摘要，调用方**不知道自己丢了文件**。
#   本仓自己的规矩就是"极慢测试应显式 @pytest.mark.timeout(N) 覆盖，不要依赖全局默认"
#   （pytest.ini 的 addtimeout 注释）。本模块正是这类测试。
# Why 取 900s 而不是"放宽门禁"：这不是质量门禁，是**进程级资源边界**。
#   900s = 实测 96s 的约 9 倍余量；断言语义一字未动，测试仍会因为路由没接线而失败。
#   配套的结构性防线（分块 + 完整性校验 + 丢文件自动补跑）见
#   scripts/run_full_pytest.py 与 docs/closeout/TEST_TIMEOUT_20260919.md。
pytestmark = pytest.mark.timeout(900)

#: 已确认**未注册**（端点由别处提供或已迁移）的路由模块 → 原因。
#: 每条都必须能在 `app_server.py` 里找不到接线、且文件仍存在；否则本测试会失败。
KNOWN_UNREGISTERED: dict[str, str] = {
    "routes_chat": "未接线；聊天端点由 plugins/chat.py 提供（url_map 中为 chat.* 命名空间）",
    "routes_monitoring": "未接线；监控端点由 plugins/status.py 与 routes_dashboard.py 提供",
    "routes_panorama": "未接线；全景面板端点由其它已注册模块提供",
    "routes_permission": "未接线；权限端点由 plugins/* 与 routes_ui_panels.py 提供",
    "routes_personality": "未接线；人格端点由 plugins/status.py 提供",
    "routes_workspace": "未接线；工作区端点由已注册模块提供",
    "extensions": "未接线；扩展端点由 plugins/* 提供（注意：本文件名不带 routes_ 前缀）",
    "routes_cli": "未接线；本文件仅 9 行占位（只打一条 info 日志）",
    "routes_computer_use": "未接线；本文件仅占位实现",
    "routes_config": "未接线；27/29 条端点由其他模块提供，语义层热更两条（/api/orchestrator/semantic-config）已单独接线",
    "routes_sessions": "未接线；`/api/handoff` 已由 app_server.py:1027 明确宣告移除（会话 API 由 plugins/chat.py 提供），全仓无调用方",
}


def _registered_in_app_server() -> set[str]:
    """`app_server.py` 里显式注册的模块名（真实注册位置）"""
    src = APP_SERVER.read_text(encoding="utf-8")
    pattern = r"from\s+agent\.server_routes\.(\w+)\s+import\s+register_routes"
    return set(re.findall(pattern, src))


def _modules_defining_register_routes() -> set[str]:
    """`agent/server_routes/*.py` 中**定义了** `register_routes` 的模块名"""
    out: set[str] = set()
    for path in sorted(SERVER_ROUTES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover 语法错的文件不归本测试管
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == "register_routes":
                out.add(path.stem)
                break
    return out


def test_每个_register_routes_模块_要么已接线_要么显式登记():
    """核心不变量：不允许存在"写了 register_routes 又没人接线还无人知晓"的模块"""
    registered = _registered_in_app_server()
    declared = _modules_defining_register_routes()
    assert registered, "未从 app_server.py 解析到任何接线（解析口径失效，本测试会假通过）"
    assert len(declared) >= 15, f"解析到的模块过少（{len(declared)}），口径可能失效"

    unaccounted = sorted(declared - registered - set(KNOWN_UNREGISTERED))
    assert not unaccounted, (
        "以下路由模块定义了 register_routes，但既未在 app_server.py 里接线、"
        f"也未登记为「已知未注册」：{unaccounted}\n"
        "→ 二选一：① 在 app_server.py 显式注册（这才是真实生效的位置）；"
        "② 加进本文件的 KNOWN_UNREGISTERED 并写明原因。")


def test_清单里的模块确实仍未接线():
    """防"清单过期"：某模块被接线后必须从清单里删掉，否则清单会变成谎话表"""
    registered = _registered_in_app_server()
    stale = sorted(m for m in KNOWN_UNREGISTERED if m in registered)
    assert not stale, f"这些模块已接线，请从 KNOWN_UNREGISTERED 中删除：{stale}"


def test_清单里的模块文件都存在():
    """防"清单指向不存在的文件"（模块被删/改名后条目要一起处理）"""
    missing = sorted(m for m in KNOWN_UNREGISTERED
                     if not (SERVER_ROUTES_DIR / f"{m}.py").exists())
    assert not missing, f"清单条目指向不存在的文件，请删除或更正：{missing}"


def test_清单条目都写明了原因():
    empty = sorted(m for m, why in KNOWN_UNREGISTERED.items() if not str(why).strip())
    assert not empty, f"这些清单条目没有写原因：{empty}"


@pytest.mark.parametrize("module", ["routes_agent_lines", "routes_approval",
                                    "routes_semantic_config"])
def test_近期接线过的模块仍在真实注册位置(module):
    """反向锁死：本会话修过的 404（主线管理 / 审批 / 语义层热更）必须留在显式注册清单里。

    它们的历史教训相同：只加进死代码 `register_all_routes` ⇒ 线上 404。
    """
    assert module in _registered_in_app_server(), (
        f"{module} 从 app_server.py 的显式注册里消失了 —— 这正是它曾 404 的原因，"
        "别把它挪回 register_all_routes")


@pytest.fixture(scope="module")
def real_url_paths() -> set:
    """真实路由表（`import app_server` 后枚举；同进程内只付一次导入代价）"""
    import app_server  # noqa: PLC0415 真实入口，与生产同一份注册代码
    return {str(r.rule) for r in app_server.app.url_map.iter_rules()}


#: 本轮修过的"曾经 404"端点 —— 它们都栽在同一个坑上：
#: 代码写了、测试用手搓 `Flask(__name__)` 注册（于是测试全绿）、线上却是 404。
RECENTLY_FIXED_ENDPOINTS = [
    "/api/memory/review",          # TLM Step 2 交付物
    "/api/vector/stats",
    "/api/vector/recent",
    "/api/vector/add",
    "/api/vector/batch_add",
    "/api/vector/item/<item_id>",
    "/api/vector/clear",
    "/api/knowledge/add",
    "/api/orchestrator/semantic-config",
]


@pytest.mark.parametrize("path", RECENTLY_FIXED_ENDPOINTS)
def test_修过的端点必须在真实路由表里(path, real_url_paths):
    """**用真实入口验证**，而不是手搓 Flask —— 手搓的那套测试在线上 404 时依然全绿。"""
    assert path in real_url_paths, (
        f"{path} 不在 app_server 的真实 url_map 里（线上会 404）——"
        "请显式注册对应模块，别只加进 register_all_routes")
