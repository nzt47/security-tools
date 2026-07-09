"""单元测试专用 conftest — 补充顶层 conftest 的 logger 隔离。

针对 tests/unit/ 套件中观察到的 flaky 问题：setup_agent_logging()
（agent/log_system/handlers.py:102）会清空 root logger 所有 handlers 并
添加 EmojiFilter/SensitiveDataFilter，导致后续测试的 emoji 断言、audit
日志断言、JSON 日志格式断言失败。三次全量运行 failures 在 6-67 间波动，
证实存在严重的 logger 状态污染。

策略对比：
- 顶层 conftest 的「快照/恢复」：快照发生在 yield 前，若前序测试已污染
  logger，快照本身就是被污染的状态，恢复无意义。
- 本 conftest 的「session 黄金状态 + function 强制恢复」：session 开始
  时拍黄金快照，每个测试前后强制恢复，确保每个测试从干净配置开始。
"""
import logging

import pytest


_GOLDEN_HANDLERS = None
_GOLDEN_LEVEL = None
_GOLDEN_HANDLER_STATE = {}  # {id(handler): (level, formatter, filters)}


def _snapshot_golden():
    """快照 root logger 的当前状态作为黄金状态。"""
    global _GOLDEN_HANDLERS, _GOLDEN_LEVEL, _GOLDEN_HANDLER_STATE
    root = logging.getLogger()
    _GOLDEN_HANDLERS = root.handlers[:]
    _GOLDEN_LEVEL = root.level
    _GOLDEN_HANDLER_STATE = {
        id(h): (h.level, h.formatter, h.filters[:])
        for h in root.handlers[:]
    }


def _restore_golden():
    """强制恢复 root logger 到黄金状态（handlers/level/filters/formatter）。

    Why: 仅恢复 handlers 引用不够——测试可能给 handler 添加了 filter 或
    改变了 level/formatter。需逐项恢复 handler 的完整状态。
    """
    if _GOLDEN_HANDLERS is None:
        return
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in _GOLDEN_HANDLERS:
        state = _GOLDEN_HANDLER_STATE.get(id(h))
        if state is not None:
            h.level, h.formatter, _saved_filters = state
            h.filters = _saved_filters[:]
        root.addHandler(h)
    root.setLevel(_GOLDEN_LEVEL)


@pytest.fixture(scope="session", autouse=True)
def _unit_logger_golden_snapshot():
    """session 开始时快照 root logger 黄金状态。

    执行时机：pytest_configure（_setup_test_logging 配置 FileHandler +
    StreamHandler）之后，第一个 unit 测试之前。此时 root logger 是干净的
    session 初始配置。
    """
    _snapshot_golden()
    yield


@pytest.fixture(scope="function", autouse=True)
def _unit_isolate_logger():
    """每个 unit 测试前后强制恢复 root logger 到黄金状态。

    执行顺序（与顶层 reset_global_singletons 协同）：
    1. 顶层 fixture yield 前快照（可能被污染）
    2. 本 fixture yield 前恢复到黄金状态（覆盖污染）
    3. 测试执行（logger 干净）
    4. 本 fixture yield 后恢复到黄金状态
    5. 顶层 fixture yield 后恢复到步骤1快照（可能被污染，但下一测试的
       步骤2会再次恢复到黄金状态）
    """
    _restore_golden()
    yield
    _restore_golden()
