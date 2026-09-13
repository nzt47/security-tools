"""集成测试收集配置

check_*.py 是运维检查脚本，在模块级直接发起 HTTP 请求连接 Prometheus。
当 Prometheus 未运行时会导致收集错误（ConnectionRefusedError）。

策略：默认跳过这些脚本的收集，通过环境变量 RUN_PROM_CHECKS=1 启用。

另含：原生扩展导入顺序固化（`_pin_native_import_order`），规避 Windows 上
      pyarrow 原生初始化导致的进程级 0xC0000005 崩溃（S10-05）。
"""
import importlib
import logging
import os
import sys
import time

import pytest

# ════════════════════════════════════════════════════════════
# 原生扩展导入顺序固化（S10-05）
# ════════════════════════════════════════════════════════════
# 【现象】单进程跑 `pytest tests/integration` 全量时，进程跑到 20%
#   （tests/integration/test_digital_life_integration.py::
#    TestModuleSafeImport::test_digital_life_initializes_with_missing_optional_modules，
#    第 194 行 `DigitalLife(config={})`）被系统直接终止，退出码 -1073741819
#    (0xC0000005 ACCESS_VIOLATION)，**没有 pytest 汇总行**，故拿不到 FAILED 列表。
# 【根因（双证据：faulthandler 栈 + Windows 应用程序错误日志）】
#   事件日志：错误模块 = site-packages\pyarrow\arrow.dll，异常代码 = 0xc0000005。
#   faulthandler 当前线程栈（自下而上）：
#     DigitalLife → agent/orchestrator/lifecycle_manager.py:118 `import sentence_transformers`
#       → sentence_transformers/util/__init__.py:26 → util/retrieval.py:14
#       → util/similarity.py:9 `import sklearn`
#       → sklearn/utils/fixes.py:19 `import pandas`
#       → pandas/compat/__init__.py:28 → pandas/compat/pyarrow.py:12 `import pyarrow`
#       → pyarrow/__init__.py:71 `from pyarrow.lib import ...`
#       （加载 lib.cp312-win_amd64.pyd → arrow.dll 原生初始化）→ ACCESS_VIOLATION
#   即：pyarrow 的**原生初始化**被推迟到进程已加载 torch / onnxruntime / sklearn 等
#   大量原生库**之后**才发生。这是 Windows 上已知的「原生 DLL 加载顺序 / 地址空间」
#   缺陷（同类问题 2026-08-09 已在 tests/unit/test_vector_store_sqlite_vec.py 处置过），
#   **不是**被测功能缺陷：同一用例单独跑 47/47 通过、连跑 3 次稳定。
# 【处置】在收集阶段的最早时机（进程最干净时）按固定顺序完成同一批原生栈的导入；
#   之后 sklearn / pandas 的 `import pyarrow` 命中 sys.modules，原生初始化不再发生，
#   崩溃路径不可达。与产品侧既有处置同源同法（lifecycle_manager.py:118 预导入
#   sentence_transformers 规避同一类崩溃）。
# 【顺序】numpy → pyarrow → pandas → sklearn：与 sklearn.utils.fixes 的真实依赖链一致，
#   保证 pyarrow 在任何重型原生库（torch/onnxruntime）之前完成原生初始化。
# 【失败姿态】任何一步失败仅降级告警，不阻断收集——环境缺件应表现为用例失败/跳过，
#   而不是整个会话崩溃（守【不易】主链路：规避逻辑不得引入新的硬依赖）。
_NATIVE_IMPORT_ORDER = ("numpy", "pyarrow", "pandas", "sklearn")
_NATIVE_PREIMPORT_RESULTS: dict[str, str] = {}


def _pin_native_import_order() -> None:
    """按固定顺序预导入原生栈，使 pyarrow 在进程干净时完成原生初始化。"""
    for _name in _NATIVE_IMPORT_ORDER:
        if _name in sys.modules:
            _NATIVE_PREIMPORT_RESULTS[_name] = "cached"
            continue
        _t0 = time.time()
        try:
            importlib.import_module(_name)
        except Exception as _e:  # pragma: no cover - 仅环境缺件时走到
            _NATIVE_PREIMPORT_RESULTS[_name] = "failed:%s" % _e
            logging.getLogger(__name__).warning(
                "[S10-05] 原生扩展预导入失败（降级，不阻断收集）: %s: %s", _name, _e
            )
        else:
            _NATIVE_PREIMPORT_RESULTS[_name] = "ok:%.1fms" % ((time.time() - _t0) * 1000)


_pin_native_import_order()

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


def pytest_report_header(config, start_path=None):
    """把原生扩展预导入结果写进报告头。

    Why（不可省）：规避逻辑若静默失败，报告里看不出「崩溃路径是否真的被规避」，
    等于假绿灯。此处把每一步的实际状态（ok/耗时、cached、failed:原因）显式打印，
    使「被检查的对象不会从报告里消失」。
    """
    detail = ", ".join(
        "%s=%s" % (k, v) for k, v in _NATIVE_PREIMPORT_RESULTS.items()
    ) or "（未执行）"
    return ["[S10-05] 原生扩展导入顺序固化: " + detail]
