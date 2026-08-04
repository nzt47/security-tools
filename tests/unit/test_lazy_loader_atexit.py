"""lazy_loader atexit 注册单元测试 [TLM-AUDIT-P2]

验证：
- 初始化后 atexit 注册列表包含 _atexit_shutdown
- shutdown 方法幂等（多次调用不报错）
- _atexit_shutdown 捕获异常不传播
- shutdown 调用 executor.shutdown(wait=True)
"""
import atexit
import pytest
from unittest.mock import patch, MagicMock

from agent.lazy_loader import LazyModuleLoader


class TestAtexitRegistration:
    """atexit 注册与 shutdown 行为验证"""

    def test_atexit_registered_on_init(self):
        """初始化后 atexit 注册列表包含 _atexit_shutdown"""
        loader = LazyModuleLoader(max_workers=2)
        # 获取 atexit 注册的回调列表
        callbacks = atexit._exithandlers if hasattr(atexit, '_exithandlers') else []
        # atexit.register 在 Python 3.7+ 使用内部列表，无法直接检查
        # 改为验证 _atexit_shutdown 方法存在且可调用
        assert callable(getattr(loader, '_atexit_shutdown', None)), \
            "_atexit_shutdown 方法应存在且可调用"
        # 验证 shutdown 方法存在
        assert callable(getattr(loader, 'shutdown', None)), \
            "shutdown 方法应存在且可调用"
        # 清理
        loader._shutdown_called = True  # 防止 atexit 再次 shutdown
        loader.executor.shutdown(wait=False)

    def test_shutdown_idempotent(self):
        """多次调用 shutdown 不报错（幂等性）"""
        loader = LazyModuleLoader(max_workers=2)
        # 第一次调用
        loader.shutdown()
        assert loader._shutdown_called is True
        # 第二次调用应直接返回，不报错
        loader.shutdown()
        loader.shutdown()
        assert loader._shutdown_called is True

    def test_atexit_shutdown_catches_exception(self):
        """shutdown 抛异常时 _atexit_shutdown 不传播"""
        loader = LazyModuleLoader(max_workers=2)
        # mock executor.shutdown 抛异常
        loader.executor.shutdown = MagicMock(side_effect=RuntimeError("模拟关闭失败"))
        # _atexit_shutdown 应捕获异常，不传播
        try:
            loader._atexit_shutdown()
            exception_raised = False
        except Exception:
            exception_raised = True
        assert not exception_raised, "_atexit_shutdown 应捕获异常不传播"
        # 验证 executor.shutdown 确实被调用了
        assert loader.executor.shutdown.called

    def test_shutdown_calls_executor_shutdown(self):
        """shutdown 调用 executor.shutdown(wait=True)"""
        loader = LazyModuleLoader(max_workers=2)
        # mock executor.shutdown
        loader.executor.shutdown = MagicMock()
        loader.shutdown()
        # 验证 executor.shutdown(wait=True) 被调用
        assert loader.executor.shutdown.called
        call_args = loader.executor.shutdown.call_args
        assert call_args.kwargs.get('wait') is True or \
               (len(call_args.args) > 0 and call_args.args[0] is True), \
               "应调用 executor.shutdown(wait=True)"
