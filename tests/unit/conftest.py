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
import os
import sys
import logging
from contextlib import ExitStack
from unittest.mock import patch

import pytest


# CI Linux 环境：chromadb/onnxruntime/hnswlib 的 manylinux wheel 编译时使用了
# GitHub Actions runner CPU 不支持的指令集（AVX2/AVX-512），触发 SIGILL
# （exit code 132）。本地 Windows 不受影响。
_CI_LINUX = sys.platform == 'linux' and bool(os.environ.get('CI'))


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


def _isolate_test_loggers():
    """清理所有动态创建的 logger 的 handlers/filters/level，恢复默认传播状态。

    Why: agent 模块的 observability.py 用 logging.getLogger("agent.<mod>")、
    scripts/visibility_report.py 用 logging.getLogger("visibility_report")
    等创建子 logger，默认无 handler、propagate=True、level=NOTSET（继承 root）。
    但某些测试可能给这些子 logger 添加了 handler/filter 或改变 level/propagate，
    导致后续测试的 caplog 捕获不到日志（如 audit trackEvent 日志被过滤、
    visibility_report 的结构化 JSON 日志被吞）。
    动态 logger 是按需创建的，session 快照抓不到，故用「强制清理」策略。

    保留 pytest 框架自身的 logger（以 "pytest" 开头），避免干扰测试框架行为。
    """
    manager = logging.Logger.manager.loggerDict
    for name, obj in list(manager.items()):
        if name.startswith("pytest"):
            continue
        if not isinstance(obj, logging.Logger):
            continue  # 跳过 Placeholder
        obj.handlers.clear()
        obj.setLevel(logging.NOTSET)
        obj.filters.clear()
        obj.propagate = True


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
    """每个 unit 测试前后强制恢复 root logger 到黄金状态，并清理所有动态 logger。

    执行顺序（与顶层 reset_global_singletons 协同）：
    1. 顶层 fixture yield 前快照（可能被污染）
    2. 本 fixture yield 前恢复到黄金状态 + 清理动态 logger（覆盖污染）
    3. caplog fixture 设置（给指定 logger 添加 handler）
    4. 测试执行（logger 干净）
    5. caplog fixture 清理
    6. 本 fixture yield 后恢复到黄金状态 + 清理动态 logger
    7. 顶层 fixture yield 后恢复到步骤1快照（可能被污染，但下一测试的
       步骤2会再次恢复到黄金状态）
    """
    _restore_golden()
    _isolate_test_loggers()
    yield
    _restore_golden()
    _isolate_test_loggers()


@pytest.fixture(scope="function", autouse=True)
def _disable_optional_systems_safety():
    """默认禁用可选系统可用性标志，避免 CI 加载 chromadb/onnxruntime 等 C 扩展触发 SIGILL。

    Why: Python 3.12 + onnxruntime（chromadb 依赖）在某些 CPU 上执行非法指令
    导致 SIGILL（exit code 132）。SIGILL 是 OS 信号，try/except 无法捕获，
    会导致整个测试进程崩溃，连带 fail-fast 取消其他 matrix 版本。单元测试
    应聚焦配置逻辑，不应实际加载重量级 C 扩展。

    显式 patch 为 True 的测试不受影响：mock.patch 嵌套时内层 patch 覆盖外层，
    内层退出后恢复到本 fixture 设置的 False，外层退出后恢复到原始值。

    CI Linux 额外防护：通过 patch.dict(sys.modules) 将 chromadb /
    sentence_transformers 设为 None，使 `import chromadb` 抛 ImportError 而非
    触发 native 扩展加载。复用业务代码已有的 ImportError fallback 路径
    （VectorStore→JSON fallback，OptimizedChromaDB→MockChromaClient），
    无需改动业务逻辑。本地 Windows 不触发此防护，仍用真实 chromadb。
    """
    with ExitStack() as stack:
        stack.enter_context(patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', False))
        stack.enter_context(patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', False))
        stack.enter_context(patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', False))
        stack.enter_context(patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', False))
        if _CI_LINUX:
            # sys.modules[name] = None 会让 `import name` 抛 ImportError，而非
            # 加载真实 native 扩展。patch.dict 退出后自动恢复 sys.modules 原状。
            stack.enter_context(patch.dict(sys.modules, {
                'chromadb': None,
                'chromadb.config': None,
                'sentence_transformers': None,
            }))
        yield


# ════════════════════════════════════════════════════════════
#  run_sandbox 测试专用：Fake spawn context
# ════════════════════════════════════════════════════════════
# Why: CI Linux spawn 方式 pickle Connection 对象时报
# `Can't pickle rebuild_connection` 错误（9个测试失败，跨 3.10/3.11）。
# 改用 threading 在当前进程中执行 target，避免子进程 pickle。
# 测试仍验证 run_sandbox 的预检查、超时处理、结果解析逻辑。
import queue as _queue_module
import threading as _threading_module


class _FakeMPQueue:
    """模拟 multiprocessing.Queue，使用线程安全 queue.Queue"""

    def __init__(self):
        self._q = _queue_module.Queue()

    def put(self, item):
        self._q.put(item)

    def get(self, timeout=None):
        return self._q.get(timeout=timeout)

    def close(self):
        pass

    def join_thread(self):
        pass


class _FakeMPProcess:
    """模拟 multiprocessing.Process，在线程中执行 target

    force_timeout=True 时不执行 target，is_alive 总返回 True，
    用于模拟超时场景（threading 无法安全终止死循环线程）。
    """

    def __init__(self, target, args=(), daemon=False, force_timeout=False):
        self._target = target
        self._args = args
        self._daemon = daemon
        self._force_timeout = force_timeout
        self._thread = None
        self.exitcode = None

    def start(self):
        if self._force_timeout:
            return
        self._thread = _threading_module.Thread(
            target=self._run, daemon=self._daemon
        )
        self._thread.start()

    def _run(self):
        try:
            self._target(*self._args)
            self.exitcode = 0
        except SystemExit as e:
            self.exitcode = e.code if isinstance(e.code, int) else 1
        except Exception:
            self.exitcode = 1

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout=timeout)

    def is_alive(self):
        if self._force_timeout:
            return True
        return self._thread is not None and self._thread.is_alive()

    def terminate(self):
        pass

    def kill(self):
        pass


class _FakeSpawnContext:
    """模拟 multiprocessing.spawn context"""

    def __init__(self):
        self.force_timeout = False

    def Queue(self):
        return _FakeMPQueue()

    def Process(self, target, args=(), daemon=False):
        return _FakeMPProcess(
            target, args, daemon, force_timeout=self.force_timeout
        )


@pytest.fixture
def mock_sandbox_spawn():
    """Mock multiprocessing.spawn context 避免 CI Linux pickle 错误。

    用法：在测试类中通过 autouse fixture 引用：
        @pytest.fixture(autouse=True)
        def _mock_spawn(self, mock_sandbox_spawn):
            self._spawn = mock_sandbox_spawn

    超时测试设置 self._spawn.force_timeout = True 模拟进程不退出。
    """
    import multiprocessing
    ctx = _FakeSpawnContext()
    with patch.object(multiprocessing, 'get_context', return_value=ctx):
        yield ctx
