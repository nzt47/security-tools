"""集成测试收集配置

check_*.py 是运维检查脚本，在模块级直接发起 HTTP 请求连接 Prometheus。
当 Prometheus 未运行时会导致收集错误（ConnectionRefusedError）。

策略：默认跳过这些脚本的收集，通过环境变量 RUN_PROM_CHECKS=1 启用。

另含：原生扩展导入顺序固化。**S11-01 起实现已提升为共用模块**
      `agent/utils/native_preimport.py`，本文件只保留 S10-05 的原调用位
      （不再内联实现，避免两份实现各自漂移）。
"""
import os
import sys

import pytest

# 兼容"裁剪入口"：`pytest tests/integration --confcutdir=tests/integration`（或裸 `pytest`
# 而非 `python -m pytest`）时，`tests/conftest.py` **不会被加载** ⇒ 它那句
# `sys.path.insert(0, PROJECT_ROOT)` 也就没执行，下面 `from agent.utils...` 会
# ModuleNotFoundError。S10-05 时本文件不 import agent（纯 collect_ignore），故没暴露；
# S11-01 改为 import 共用实现后就暴露了。此处补一句幂等兜底（基线在该组合下本就
# 有 48 个 collection error，这里只是不让它**更糟**：conftest 层直接 ImportError）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ════════════════════════════════════════════════════════════
# 原生扩展导入顺序固化（S10-05 引入 → S11-01 提升为进程入口共用实现）
# ════════════════════════════════════════════════════════════
# 【现象/根因/处置的完整记述已随实现搬到】`agent/utils/native_preimport.py`
#   的模块 docstring（faulthandler 栈、arrow.dll 事件日志、顺序依据、失败姿态）。
#   此处**不复制**那段记述，只保留"为什么这里还要调一次"。
# 【为什么改为 import 而不保留副本（不易）】S11-01 要求 `app_server.py` 进程
#   入口与 `tests/**` 共用**同一份**实现。两份实现必然各自漂移：顺序或开关语义
#   只改一处 ⇒ 另一边**静默**失去保护（且守卫看不出来）。故本文件只 import。
# 【为什么这里仍然调用一次】`tests/conftest.py` 已在更早时机跑过同一函数，且
#   该函数**幂等**（第二次只返回首次结果、不重跑、不覆写耗时）。保留本调用位是
#   为了让"只收集 integration 子目录"的入口（含 `--confcutdir` 等裁剪场景）也必然
#   命中，不依赖上层 conftest 的加载顺序。
from agent.utils.native_preimport import (  # noqa: E402
    pin_native_import_order,
)

pin_native_import_order()

if os.environ.get("RUN_PROM_CHECKS", "0") != "1":
    collect_ignore = [
        "check_5xx_source.py",
        "check_baseline.py",
        "check_targets.py",
    ]


@pytest.fixture
def ab_test_manager(tmp_path):
    """每个测试独立的 ABTestManager，使用临时 SQLite 隔离。"""
    from agent.ab_testing import ABTestManager
    mgr = ABTestManager(storage_path=str(tmp_path / "ab_testing"))
    mgr.initialize()
    yield mgr


@pytest.fixture
def feedback_manager(tmp_path):
    """每个测试独立的 FeedbackManager，使用临时 SQLite 隔离。"""
    from agent.feedback import FeedbackManager
    mgr = FeedbackManager(storage_path=str(tmp_path / "feedback"))
    mgr.initialize()
    yield mgr


@pytest.fixture
def skills_mgmt_client():
    """构造最小 Flask app + TestClient，mock 服务层。

    返回 (client, mock_svc) 元组，测试可配置 mock_svc 的返回值。
    """
    from flask import Flask
    from unittest.mock import MagicMock, patch
    from agent.server_routes.routes_skills_mgmt import register_routes

    mock_svc = MagicMock()
    patches = [
        patch(
            "agent.server_routes.routes_skills_mgmt.get_skills_mgmt_service",
            return_value=mock_svc,
        ),
        patch(
            "agent.server_routes.routes_skills_mgmt.require_token",
            lambda f: f,
        ),
    ]
    for p in patches:
        p.start()

    app = Flask(__name__)
    app.config.update(TESTING=True)
    state = type("_S", (), {})()
    register_routes(app, state)
    client = app.test_client()

    yield client, mock_svc

    for p in patches:
        p.stop()


# 【报告头 hook 去向（S11-01）】原 S10-05 在本文件定义的
#   `pytest_report_header`（打印原生预导入结果）已**上移**到 `tests/conftest.py`。
#   原因：unit / integration 共用同一道保护后，报告头也应只有一份 —— 若两处都定义，
#   pytest 会**两次**调用该 hook，integration 跑一次会打印两遍同一行；而 unit
#   一遍也没有（unit 不进本文件）。上移后 unit / integration 都恰好一行，
#   措辞统一取自 `agent/utils/native_preimport.py::report_line()`。

