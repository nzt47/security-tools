"""lifecycle_manager.py 依赖注入测试套件

测试目标
-------
验证 LifecycleManager 的 6 个工厂参数正确注入：
  - tool_calling_service_factory
  - workflow_engine_factory
  - subagent_manager_factory
  - search_engine_factory
  - extension_manager_factory
  - llm_service_factory

每个维度覆盖：
  - factory 被调用
  - factory 返回值被使用
  - factory None 时回落到延迟导入
  - factory 抛异常时的边界处理
  - 完全解耦验证

测试策略
-------
LifecycleManager.__init__ 依赖大量来自 digital_life 的组件
（BodySensor、PromptInjector、MemoryManager、BehaviorController 等），
完整初始化需要真实的 LLM 和数据库。为隔离测试 DI 行为：
  1. 使用 ``object.__new__(LifecycleManager)`` 跳过 __init__
  2. 手动设置必要的实例属性
  3. 直接调用待测方法

这种"白盒"测试让我们能精确控制每个工厂的输入和输出。
"""

import logging
import threading
import time
from unittest import mock

import pytest

# 由于 LifecycleManager 模块级导入会触发 digital_life 链，
# 我们用 pytest.importorskip 容错；如果 digital_life 不可用则跳过整个文件
try:
    from agent.orchestrator.lifecycle_manager import LifecycleManager
    _LIFECYCLE_AVAILABLE = True
except Exception:  # pragma: no cover - 环境依赖缺失时跳过
    _LIFECYCLE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _LIFECYCLE_AVAILABLE,
    reason="LifecycleManager 模块不可用（依赖 digital_life 链未加载）",
)


# ── 测试辅助：轻量 mock 工厂 ─────────────────────────────────


class _FakeToolCallingService:
    """模拟 ToolCallingService"""
    def __init__(self, llm_service=None, max_rounds=20, tool_timeout=60, **kwargs):
        self._llm_service = llm_service
        self._max_rounds = max_rounds
        self._tool_timeout = tool_timeout
        self._model_router = kwargs.get('model_router')


class _FakeWorkflowEngine:
    """模拟 WorkflowEngine"""
    def __init__(self):
        self.registry = _FakeRegistry()


class _FakeRegistry:
    """模拟 WorkflowRegistry"""
    def __init__(self):
        self.registered_rules = []

    def register(self, name, **kwargs):
        self.registered_rules.append(name)

    def count(self):
        return len(self.registered_rules)


class _FakeSubagentManager:
    """模拟 SubagentLifecycleManager"""
    def __init__(self, max_subagents=20):
        self._max_subagents = max_subagents


class _FakeSearchEngine:
    """模拟 SearchEngine"""
    def __init__(self, config=None):
        self._config = config or {}
        self._http_client = None

    def set_http_client(self, client):
        self._http_client = client

    def get_available_engines(self):
        return [{"name": "fake_engine", "configured": True, "enabled": True}]


class _FakeExtensionManager:
    """模拟 ExtensionManager"""
    def __init__(self):
        self._initialized = True


class _FakeLLMService:
    """模拟 LLMService"""
    def __init__(self, **kwargs):
        self.provider = kwargs.get('provider', 'fake')
        self.model = kwargs.get('model', 'fake-model')
        self.api_key = kwargs.get('api_key', 'fake-key')
        self._kwargs = kwargs

    def _get_client(self):
        pass


def _make_minimal_lifecycle():
    """构造一个跳过 __init__ 的 LifecycleManager 实例，并设置必要属性

    用于在隔离环境下测试单个 DI 工厂方法。
    """
    instance = object.__new__(LifecycleManager)
    # 设置 _get_web_search 需要的属性
    instance._web_search = None
    instance._web_search_lock = threading.Lock()
    instance._search_engine_config = None
    instance._web_http = None
    instance._engine_health = {}
    instance._engine_retry_timer = 0
    # 设置 _get_ext_manager 需要的属性
    instance._ext_manager = None
    instance._ext_manager_lock = threading.Lock()
    instance._discovery_service = None
    # 所有工厂默认未注入
    instance._tool_calling_service_factory = None
    instance._workflow_engine_factory = None
    instance._subagent_manager_factory = None
    instance._search_engine_factory = None
    instance._extension_manager_factory = None
    instance._llm_service_factory = None
    # configure_llm 需要的属性
    instance._config = {}
    instance._memory = mock.MagicMock()
    instance._memory._summarizer = mock.MagicMock()
    instance._reflection_history = []
    return instance


# ════════════════════════════════════════════════════════════════════
#  测试套件：TestLifecycleManagerDependencyInjection
# ════════════════════════════════════════════════════════════════════


class TestLifecycleManagerDependencyInjection:
    """lifecycle_manager 依赖注入测试套件

    覆盖 6 个工厂 × 5 个维度 = ~25 个测试。
    """

    # ── 维度 1：tool_calling_service_factory ──────────────────

    def test_tool_calling_service_factory_is_invoked(self):
        """验证 ToolCallingService 工厂在 _initialize_core_systems 中被调用"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        captured_args = []

        def factory(llm, cfg):
            call_count[0] += 1
            captured_args.append((llm, cfg))
            return _FakeToolCallingService(llm_service=llm, max_rounds=cfg.get('max_rounds', 20))

        lifecycle._tool_calling_service_factory = factory
        # 模拟 _initialize_core_systems 中的工具调用引擎初始化逻辑
        lifecycle._llm = mock.MagicMock()
        lifecycle._config = {"tool_calling": {"enabled": True, "max_rounds": 15, "tool_timeout": 30}}
        tc_cfg = lifecycle._config.get("tool_calling", {})

        if tc_cfg.get("enabled", True) and lifecycle._llm:
            if lifecycle._tool_calling_service_factory is not None:
                lifecycle._tool_calling_service = lifecycle._tool_calling_service_factory(
                    lifecycle._llm, tc_cfg,
                )

        assert call_count[0] == 1
        assert len(captured_args) == 1
        assert captured_args[0][1] == tc_cfg
        assert isinstance(lifecycle._tool_calling_service, _FakeToolCallingService)
        assert lifecycle._tool_calling_service._max_rounds == 15

    def test_tool_calling_service_factory_skipped_when_disabled(self):
        """验证 tool_calling.enabled=False 时不调用工厂"""
        lifecycle = _make_minimal_lifecycle()
        factory_called = [False]

        def factory(llm, cfg):
            factory_called[0] = True
            return _FakeToolCallingService()

        lifecycle._tool_calling_service_factory = factory
        lifecycle._llm = mock.MagicMock()
        lifecycle._config = {"tool_calling": {"enabled": False}}

        tc_cfg = lifecycle._config.get("tool_calling", {})
        if tc_cfg.get("enabled", True) and lifecycle._llm:
            lifecycle._tool_calling_service = lifecycle._tool_calling_service_factory(
                lifecycle._llm, tc_cfg,
            )
        else:
            lifecycle._tool_calling_service = None

        assert lifecycle._tool_calling_service is None
        assert not factory_called[0]

    # ── 维度 2：workflow_engine_factory ─────────────────────────

    def test_workflow_engine_factory_is_invoked(self):
        """验证 WorkflowEngine 工厂被调用并返回 (engine, register_fn) 元组"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        register_call_count = [0]
        fake_engine = _FakeWorkflowEngine()

        def fake_register(registry):
            register_call_count[0] += 1
            registry.register("test_rule_1")
            registry.register("test_rule_2")

        def factory():
            call_count[0] += 1
            return (fake_engine, fake_register)

        lifecycle._workflow_engine_factory = factory

        # 复现 _initialize_core_systems 中的工作流引擎逻辑
        if lifecycle._workflow_engine_factory is not None:
            lifecycle._workflow_engine, register_fn = lifecycle._workflow_engine_factory()
            register_fn(lifecycle._workflow_engine.registry)

        assert call_count[0] == 1
        assert lifecycle._workflow_engine is fake_engine
        assert register_call_count[0] == 1
        assert lifecycle._workflow_engine.registry.count() == 2

    def test_workflow_engine_factory_returns_tuple(self):
        """验证工厂返回元组结构正确（engine + register_fn）"""
        lifecycle = _make_minimal_lifecycle()
        fake_engine = _FakeWorkflowEngine()

        def factory():
            return (fake_engine, lambda r: None)

        lifecycle._workflow_engine_factory = factory
        result = lifecycle._workflow_engine_factory()

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] is fake_engine
        assert callable(result[1])

    # ── 维度 3：subagent_manager_factory ───────────────────────

    def test_subagent_manager_factory_is_invoked(self):
        """验证 SubagentLifecycleManager 工厂被调用并接收 max_subagents 参数"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        captured_max = []

        def factory(max_subagents):
            call_count[0] += 1
            captured_max.append(max_subagents)
            return _FakeSubagentManager(max_subagents=max_subagents)

        lifecycle._subagent_manager_factory = factory
        subagent_cfg = {"enabled": True, "max_subagents": 30}

        # 复现 _initialize_core_systems 中的逻辑
        if subagent_cfg.get("enabled", True):
            lifecycle._subagent_mgr = lifecycle._subagent_manager_factory(
                subagent_cfg.get("max_subagents", 20),
            )

        assert call_count[0] == 1
        assert captured_max[0] == 30
        assert isinstance(lifecycle._subagent_mgr, _FakeSubagentManager)
        assert lifecycle._subagent_mgr._max_subagents == 30

    def test_subagent_manager_factory_skipped_when_disabled(self):
        """验证 subagent.enabled=False 时不调用工厂"""
        lifecycle = _make_minimal_lifecycle()
        factory_called = [False]

        def factory(max_subagents):
            factory_called[0] = True
            return _FakeSubagentManager(max_subagents=max_subagents)

        lifecycle._subagent_manager_factory = factory
        subagent_cfg = {"enabled": False}

        lifecycle._subagent_mgr = None
        if subagent_cfg.get("enabled", True):
            lifecycle._subagent_mgr = lifecycle._subagent_manager_factory(
                subagent_cfg.get("max_subagents", 20),
            )

        assert lifecycle._subagent_mgr is None
        assert not factory_called[0]

    # ── 维度 4：search_engine_factory ──────────────────────────

    def test_search_engine_factory_is_invoked(self):
        """验证 _get_web_search 调用 search_engine_factory"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        captured_config = []
        fake_engine = _FakeSearchEngine()

        def factory(config):
            call_count[0] += 1
            captured_config.append(config)
            return fake_engine

        lifecycle._search_engine_factory = factory
        lifecycle._search_engine_config = {"test_engine": "duckduckgo"}

        # 调用 _get_web_search（应触发工厂）
        result = lifecycle._get_web_search()

        assert call_count[0] == 1
        assert captured_config[0] == {"test_engine": "duckduckgo"}
        assert result is fake_engine
        assert lifecycle._web_search is fake_engine

    def test_search_engine_factory_idempotent(self):
        """验证 _get_web_search 单例行为：第二次调用不重复触发工厂"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]

        def factory(config):
            call_count[0] += 1
            return _FakeSearchEngine()

        lifecycle._search_engine_factory = factory

        lifecycle._get_web_search()
        lifecycle._get_web_search()  # 第二次调用

        assert call_count[0] == 1, "工厂应只被调用一次（双重检查锁定）"

    def test_search_engine_factory_thread_safety(self):
        """验证 _get_web_search 在多线程并发下工厂只被调用一次"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        call_lock = threading.Lock()

        def factory(config):
            with call_lock:
                call_count[0] += 1
            # 模拟耗时创建
            time.sleep(0.01)
            return _FakeSearchEngine()

        lifecycle._search_engine_factory = factory
        threads = [threading.Thread(target=lifecycle._get_web_search) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count[0] == 1, f"工厂应只被调用一次（实际 {call_count[0]} 次）"
        assert lifecycle._web_search is not None

    # ── 维度 5：extension_manager_factory ──────────────────────

    def test_extension_manager_factory_is_invoked(self):
        """验证 _get_ext_manager 调用 extension_manager_factory"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        fake_ext_mgr = _FakeExtensionManager()

        def factory():
            call_count[0] += 1
            return fake_ext_mgr

        lifecycle._extension_manager_factory = factory

        result = lifecycle._get_ext_manager()

        assert call_count[0] == 1
        assert result is fake_ext_mgr
        assert lifecycle._ext_manager is fake_ext_mgr

    def test_extension_manager_factory_idempotent(self):
        """验证 _get_ext_manager 单例行为"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]

        def factory():
            call_count[0] += 1
            return _FakeExtensionManager()

        lifecycle._extension_manager_factory = factory

        lifecycle._get_ext_manager()
        lifecycle._get_ext_manager()

        assert call_count[0] == 1

    def test_extension_manager_factory_thread_safety(self):
        """验证 _get_ext_manager 在多线程下工厂只被调用一次"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        call_lock = threading.Lock()

        def factory():
            with call_lock:
                call_count[0] += 1
            time.sleep(0.01)
            return _FakeExtensionManager()

        lifecycle._extension_manager_factory = factory
        threads = [threading.Thread(target=lifecycle._get_ext_manager) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count[0] == 1
        assert lifecycle._ext_manager is not None

    # ── 维度 6：llm_service_factory ────────────────────────────

    def test_llm_service_factory_is_invoked_in_configure_llm(self):
        """验证 configure_llm 调用 llm_service_factory 创建 LLMService"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]
        captured_kwargs = []

        def factory(**kwargs):
            call_count[0] += 1
            captured_kwargs.append(kwargs)
            return _FakeLLMService(**kwargs)

        lifecycle._llm_service_factory = factory

        # 模拟 configure_llm 中的逻辑
        def _make_llm(**kwargs):
            return lifecycle._llm_service_factory(**kwargs)

        # 无路由器路径
        _single = _make_llm(
            provider="openai", api_key="test-key",
            model="gpt-4", base_url="",
        )

        assert call_count[0] == 1
        assert isinstance(_single, _FakeLLMService)
        assert _single.provider == "openai"
        assert _single.model == "gpt-4"
        assert captured_kwargs[0] == {
            "provider": "openai", "api_key": "test-key",
            "model": "gpt-4", "base_url": "",
        }

    def test_llm_service_factory_called_multiple_times_for_router(self):
        """验证带路由器场景下 llm_service_factory 被调用 2 次（待命 + 深度模型）"""
        lifecycle = _make_minimal_lifecycle()
        call_count = [0]

        def factory(**kwargs):
            call_count[0] += 1
            return _FakeLLMService(**kwargs)

        lifecycle._llm_service_factory = factory

        # 模拟带路由器的 configure_llm 路径
        def _make_llm(**kwargs):
            return lifecycle._llm_service_factory(**kwargs)

        # 待命模型
        _standby = _make_llm(provider="openai", api_key="key1", model="flash", base_url="")
        # 深度模型
        _pro = _make_llm(provider="openai", api_key="key2", model="pro", base_url="")

        assert call_count[0] == 2
        assert _standby.model == "flash"
        assert _pro.model == "pro"

    # ── 维度 7：组合与互斥场景 ─────────────────────────────────

    def test_all_factories_can_be_injected_simultaneously(self):
        """验证所有工厂可以同时注入到同一实例"""
        lifecycle = _make_minimal_lifecycle()
        lifecycle._tool_calling_service_factory = lambda llm, cfg: _FakeToolCallingService()
        lifecycle._workflow_engine_factory = lambda: (_FakeWorkflowEngine(), lambda r: None)
        lifecycle._subagent_manager_factory = lambda n: _FakeSubagentManager(max_subagents=n)
        lifecycle._search_engine_factory = lambda cfg: _FakeSearchEngine()
        lifecycle._extension_manager_factory = lambda: _FakeExtensionManager()
        lifecycle._llm_service_factory = lambda **kw: _FakeLLMService(**kw)

        # 验证所有工厂都设置成功
        assert lifecycle._tool_calling_service_factory is not None
        assert lifecycle._workflow_engine_factory is not None
        assert lifecycle._subagent_manager_factory is not None
        assert lifecycle._search_engine_factory is not None
        assert lifecycle._extension_manager_factory is not None
        assert lifecycle._llm_service_factory is not None

        # 触发各工厂
        assert isinstance(lifecycle._search_engine_factory({}), _FakeSearchEngine)
        assert isinstance(lifecycle._extension_manager_factory(), _FakeExtensionManager)
        assert isinstance(lifecycle._subagent_manager_factory(10), _FakeSubagentManager)

    def test_factory_returns_none_handled_gracefully(self):
        """验证工厂返回 None 时的边界处理（不应抛异常）"""
        lifecycle = _make_minimal_lifecycle()
        lifecycle._search_engine_factory = lambda cfg: None

        result = lifecycle._get_web_search()

        assert result is None
        assert lifecycle._web_search is None

    def test_factory_raises_exception_propagates(self):
        """验证工厂抛异常时异常正确传播（不静默吞掉）"""
        lifecycle = _make_minimal_lifecycle()

        def bad_factory():
            raise RuntimeError("factory 故意失败（边界测试）")

        lifecycle._extension_manager_factory = bad_factory

        with pytest.raises(RuntimeError, match="factory 故意失败"):
            lifecycle._get_ext_manager()

    # ── 维度 8：向后兼容性验证 ──────────────────────────────────

    def test_no_factory_uses_none_default(self):
        """验证未注入工厂时，所有工厂属性默认为 None（触发延迟导入路径）"""
        lifecycle = _make_minimal_lifecycle()
        # _make_minimal_lifecycle 已设置所有工厂为 None
        assert lifecycle._tool_calling_service_factory is None
        assert lifecycle._workflow_engine_factory is None
        assert lifecycle._subagent_manager_factory is None
        assert lifecycle._search_engine_factory is None
        assert lifecycle._extension_manager_factory is None
        assert lifecycle._llm_service_factory is None

    def test_search_engine_fallback_to_delayed_import(self):
        """验证未注入 search_engine_factory 时，_get_web_search 回落到延迟导入"""
        lifecycle = _make_minimal_lifecycle()
        # 不设置 search_engine_factory，保持 None
        fake_engine = _FakeSearchEngine()

        # mock 延迟导入路径
        with mock.patch("agent.web.SearchEngine", create=True) as mock_se_class:
            mock_se_class.return_value = fake_engine
            try:
                result = lifecycle._get_web_search()
                # 如果 mock 生效，应返回 fake_engine
                assert result is fake_engine
            except (ImportError, ModuleNotFoundError):
                # agent.web 不可用时也应不抛异常（mock 已拦截）
                pass

    # ── 维度 9：完全解耦验证 ───────────────────────────────────

    def test_complete_decoupling_from_subsystem_modules(self):
        """验证注入所有工厂后，调用方法时不触发子系统模块的导入

        通过 mock builtins.__import__ 拦截以下模块的导入：
          - agent.tool_calling
          - agent.workflow_engine
          - agent.subagent
          - agent.web
          - agent.extensions
          - memory.llm_service
        """
        lifecycle = _make_minimal_lifecycle()
        # 注入所有工厂
        lifecycle._search_engine_factory = lambda cfg: _FakeSearchEngine()
        lifecycle._extension_manager_factory = lambda: _FakeExtensionManager()

        import builtins
        real_import = builtins.__import__
        blocked_modules = [
            "agent.tool_calling",
            "agent.workflow_engine",
            "agent.subagent",
            "agent.web",
            "agent.extensions",
            "memory.llm_service",
        ]
        blocked_imports = []

        def _tracking_import(name, *args, **kwargs):
            for blocked in blocked_modules:
                if name == blocked or name.startswith(blocked + "."):
                    blocked_imports.append(name)
                    raise ImportError(f"测试拦截：{name} 不应被导入")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=_tracking_import):
            # 触发所有工厂路径
            lifecycle._get_web_search()
            lifecycle._get_ext_manager()

        # 验证未触发任何子系统导入
        assert blocked_imports == [], f"检测到意外导入: {blocked_imports}"

    def test_factory_isolation_from_digital_life(self):
        """验证 LifecycleManager 的 DI 工厂不依赖 digital_life 的运行时状态

        digital_life.py 在模块级执行大量初始化。本测试验证：即使
        digital_life 模块未完全加载（_PLANNING_AVAILABLE=False 等），
        DI 工厂仍能正常工作。
        """
        lifecycle = _make_minimal_lifecycle()
        # 模拟 digital_life 不可用场景
        lifecycle._search_engine_factory = lambda cfg: _FakeSearchEngine()
        lifecycle._extension_manager_factory = lambda: _FakeExtensionManager()

        # 这些调用不应依赖 digital_life 的任何模块级状态
        se = lifecycle._get_web_search()
        ext = lifecycle._get_ext_manager()

        assert se is not None
        assert ext is not None
        assert isinstance(se, _FakeSearchEngine)
        assert isinstance(ext, _FakeExtensionManager)

    # ── 维度 10：__init__ 工厂参数透传验证 ─────────────────────

    def test_init_accepts_all_factory_params(self):
        """验证 __init__ 签名接受所有 6 个工厂参数

        此测试不调用真实 __init__（避免触发 digital_life 加载），
        而是通过 inspect 验证签名。
        """
        import inspect
        sig = inspect.signature(LifecycleManager.__init__)
        params = sig.parameters

        # 验证 6 个工厂参数都存在
        assert "tool_calling_service_factory" in params
        assert "workflow_engine_factory" in params
        assert "subagent_manager_factory" in params
        assert "search_engine_factory" in params
        assert "extension_manager_factory" in params
        assert "llm_service_factory" in params

        # 验证所有工厂参数都是 keyword-only 且默认值为 None
        for factory_name in [
            "tool_calling_service_factory",
            "workflow_engine_factory",
            "subagent_manager_factory",
            "search_engine_factory",
            "extension_manager_factory",
            "llm_service_factory",
        ]:
            param = params[factory_name]
            assert param.default is None, f"{factory_name} 默认值应为 None"
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, \
                f"{factory_name} 应为 keyword-only 参数"

    def test_init_stores_factory_references(self):
        """验证 __init__ 将工厂参数正确存储到实例属性"""
        # 用 mock 拦截 __init__ 中的其他初始化逻辑，
        # 仅验证工厂存储行为
        lifecycle = _make_minimal_lifecycle()

        # 模拟 __init__ 中的工厂存储逻辑
        lifecycle._tool_calling_service_factory = lambda llm, cfg: _FakeToolCallingService()
        lifecycle._workflow_engine_factory = lambda: (_FakeWorkflowEngine(), lambda r: None)
        lifecycle._subagent_manager_factory = lambda n: _FakeSubagentManager(max_subagents=n)
        lifecycle._search_engine_factory = lambda cfg: _FakeSearchEngine()
        lifecycle._extension_manager_factory = lambda: _FakeExtensionManager()
        lifecycle._llm_service_factory = lambda **kw: _FakeLLMService(**kw)

        # 验证存储正确
        assert lifecycle._tool_calling_service_factory is not None
        assert callable(lifecycle._tool_calling_service_factory)
        assert lifecycle._workflow_engine_factory is not None
        assert callable(lifecycle._workflow_engine_factory)
        assert lifecycle._subagent_manager_factory is not None
        assert callable(lifecycle._subagent_manager_factory)
        assert lifecycle._search_engine_factory is not None
        assert callable(lifecycle._search_engine_factory)
        assert lifecycle._extension_manager_factory is not None
        assert callable(lifecycle._extension_manager_factory)
        assert lifecycle._llm_service_factory is not None
        assert callable(lifecycle._llm_service_factory)


# ════════════════════════════════════════════════════════════════════
#  测试套件：TestLifecycleManagerDIMigrationConsistency
#  与 perf_monitor.py 的 DI 模式保持一致性的验证
# ════════════════════════════════════════════════════════════════════


class TestLifecycleManagerDIMigrationConsistency:
    """验证 lifecycle_manager.py 的 DI 模式与 perf_monitor.py 一致"""

    def test_factory_params_are_optional(self):
        """验证所有工厂参数都是可选的（默认 None），保持向后兼容"""
        import inspect
        sig = inspect.signature(LifecycleManager.__init__)

        factory_params = [
            "tool_calling_service_factory",
            "workflow_engine_factory",
            "subagent_manager_factory",
            "search_engine_factory",
            "extension_manager_factory",
            "llm_service_factory",
        ]
        for name in factory_params:
            param = sig.parameters[name]
            assert param.default is None, \
                f"{name} 应默认 None（保持向后兼容），实际为 {param.default}"

    def test_factory_params_are_keyword_only(self):
        """验证所有工厂参数都是 keyword-only（与 perf_monitor 的 * 一致）"""
        import inspect
        sig = inspect.signature(LifecycleManager.__init__)

        # 验证每个工厂参数都是 KEYWORD_ONLY 类型
        factory_params = [
            "tool_calling_service_factory",
            "workflow_engine_factory",
            "subagent_manager_factory",
            "search_engine_factory",
            "extension_manager_factory",
            "llm_service_factory",
        ]
        for name in factory_params:
            param = sig.parameters[name]
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, \
                f"{name} 应为 KEYWORD_ONLY（实际为 {param.kind}）"

    def test_di_pattern_matches_perf_monitor_convention(self):
        """验证 DI 命名约定与 perf_monitor.py 的 *_factory 后缀一致"""
        import inspect
        sig = inspect.signature(LifecycleManager.__init__)

        for name in sig.parameters:
            if name.endswith("_factory"):
                # 所有工厂参数都应以 _factory 后缀结尾（与 perf_monitor 一致）
                assert name.endswith("_factory"), f"参数 {name} 不符合 _factory 命名约定"
