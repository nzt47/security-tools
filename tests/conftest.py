"""
pytest配置文件
云枢(Yunshu)系统自动化测试框架 - conftest.py

提供：
- 测试fixtures（测试数据和依赖注入）
- pytest钩子函数（测试执行生命周期管理）
- 测试数据管理策略
- 测试环境配置
"""

import os
import sys
import json
import copy
import time
import tempfile
import pytest
from unittest.mock import Mock

# ── Windows GBK 编码兼容：避免 emoji 日志吐乱码 ──
# 注释：不要在这里 reconfig stdout/stderr，会导致 pytest 的 capture 模块冲突
# 改用 PYTHONIOENCODING=utf-8 环境变量或直接在调用时设置
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 测试配置
TEST_CONFIG = {
    "env": os.getenv("TEST_ENV", "development"),
    "enable_monitoring": True,
    "enable_coverage": True,
    "test_data_dir": PROJECT_ROOT / "tests" / "fixtures",
    "report_dir": PROJECT_ROOT / "test_reports",
    "coverage_threshold": 70,
}

# ============================================================================
# pytest钩子函数 - 测试执行生命周期管理
# ============================================================================

def pytest_configure(config):
    """pytest配置初始化"""
    # 注册自定义标记
    config.addinivalue_line(
        "markers", "p0: P0优先级测试用例，必须通过"
    )
    config.addinivalue_line(
        "markers", "p1: P1优先级测试用例"
    )
    config.addinivalue_line(
        "markers", "requires_setup: 需要复杂环境设置"
    )

    # 创建测试报告目录
    TEST_CONFIG["report_dir"].mkdir(exist_ok=True, parents=True)

    # 设置测试日志
    _setup_test_logging(config)

def pytest_collection_modifyitems(config, items):
    """修改测试用例集合 - 合并自动标记和跳过逻辑"""
    skip_slow = pytest.mark.skip(reason="需要 --runslow 选项才能运行慢速测试")
    skip_llm = pytest.mark.skip(reason="需要 LLM 服务才能运行")

    for item in items:
        # 自动标记快速测试
        if "quick" not in item.keywords and "slow" not in item.keywords:
            if "test_basics" in item.nodeid or "test_import" in item.nodeid:
                item.add_marker(pytest.mark.quick)

        # 自动标记P0测试
        if "test_memory" in item.nodeid or "test_permission" in item.nodeid:
            item.add_marker(pytest.mark.p0)
            item.add_marker(pytest.mark.critical)

        # 跳过慢速测试（除非 --runslow）
        if "slow" in item.keywords and not config.getoption("--runslow"):
            item.add_marker(skip_slow)

        # 跳过需要 LLM 的测试（除非有 API key）
        if "requires_llm" in item.keywords and not os.getenv("LLM_API_KEY"):
            item.add_marker(skip_llm)

def pytest_runtest_makereport(item, call):
    """生成测试报告"""
    if call.when == "call":
        # 记录测试结果用于后续分析
        outcome = getattr(call, "outcome", None)
        if outcome and hasattr(call, "excinfo"):
            if call.excinfo:
                _handle_test_failure(item, call)

def _setup_test_logging(config):
    """配置测试日志"""
    log_dir = TEST_CONFIG["report_dir"] / "logs"
    log_dir.mkdir(exist_ok=True, parents=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)8s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_dir / f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
            logging.StreamHandler()
        ]
    )

def _handle_test_failure(item, call):
    """处理测试失败"""
    logger = logging.getLogger("test.failures")
    logger.error(
        f"测试失败: {item.nodeid}\n"
        f"异常: {call.excinfo.typename if call.excinfo else 'None'}\n"
        f"消息: {str(call.excinfo.value) if call.excinfo else 'None'}"
    )

# ============================================================================
# 测试Fixtures - 依赖注入
# ============================================================================

@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return PROJECT_ROOT

@pytest.fixture(scope="session")
def test_config():
    """测试配置"""
    return TEST_CONFIG

@pytest.fixture(scope="function")
def temp_test_dir(tmp_path):
    """临时测试目录"""
    test_dir = tmp_path / "test_data"
    test_dir.mkdir()
    return test_dir

@pytest.fixture(scope="function")
def sample_sensor_data():
    """示例传感器数据"""
    return {
        "cpu_usage": 45.5,
        "memory_usage": 62.3,
        "temperature": 55.0,
        "battery_level": 85,
        "disk_usage": 50.0,
        "network_status": "connected",
        "timestamp": datetime.now().isoformat()
    }

@pytest.fixture(scope="function")
def sample_memory_data():
    """示例记忆数据"""
    return {
        "sources": [
            {
                "id": "src_001",
                "type": "conversation",
                "content": "用户询问天气",
                "timestamp": datetime.now().isoformat()
            }
        ],
        "topics": [
            {
                "id": "topic_001",
                "name": "weather",
                "count": 5
            }
        ],
        "summary": {
            "content": "用户关注天气信息",
            "confidence": 0.85
        }
    }

@pytest.fixture(scope="function")
def mock_llm_response():
    """模拟LLM响应数据"""
    return {
        "response": "今天的天气晴朗，温度25度。",
        "tokens_used": 150,
        "model": "gpt-3.5-turbo",
        "finish_reason": "stop"
    }

@pytest.fixture(scope="function")
def test_user_input():
    """测试用户输入数据"""
    return {
        "message": "今天天气怎么样？",
        "user_id": "test_user_001",
        "session_id": "test_session_001",
        "timestamp": datetime.now().isoformat(),
        "metadata": {
            "platform": "test",
            "version": "2.0.0"
        }
    }

@pytest.fixture(scope="function")
def permission_test_cases():
    """权限系统测试用例"""
    return [
        {
            "name": "危险操作_删除系统文件",
            "operation": "delete",
            "path": "C:\\Windows\\System32",
            "expected_result": "blocked",
            "severity": "critical"
        },
        {
            "name": "安全操作_读取文档",
            "operation": "read",
            "path": "C:\\Users\\Documents\\report.txt",
            "expected_result": "allowed",
            "severity": "low"
        },
        {
            "name": "警告操作_修改系统配置",
            "operation": "write",
            "path": "C:\\Program Files",
            "expected_result": "warning",
            "severity": "medium"
        }
    ]

@pytest.fixture(scope="function")
def monitoring_metrics_sample():
    """监控系统指标样本数据"""
    return {
        "request_count": 100,
        "error_count": 5,
        "avg_latency_ms": 250.5,
        "max_latency_ms": 1500,
        "min_latency_ms": 50,
        "cpu_usage": 45.0,
        "memory_usage": 60.0,
        "active_connections": 10
    }

# ============================================================================
# 测试数据管理
# ============================================================================

class TestDataManager:
    """测试数据管理器"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._cache = {}

    def load_json(self, filename: str) -> Dict[str, Any]:
        """加载JSON测试数据"""
        if filename in self._cache:
            return self._cache[filename]

        filepath = self.data_dir / filename
        if not filepath.exists():
            pytest.fail(f"测试数据文件不存在: {filepath}")

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self._cache[filename] = data
        return data

    def save_json(self, filename: str, data: Dict[str, Any]):
        """保存JSON测试数据"""
        filepath = self.data_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_fixtures_path(self, fixture_name: str) -> Path:
        """获取测试固件路径"""
        return self.data_dir / "fixtures" / fixture_name

@pytest.fixture(scope="session")
def test_data_manager():
    """测试数据管理器fixture"""
    data_dir = TEST_CONFIG["test_data_dir"]
    return TestDataManager(data_dir)

# ============================================================================
# 测试环境管理
# ============================================================================

# CI 上验证 EnvConfigManager 自身 .env 文件写入/审计日志/权限行为的测试，
# 契约是真实文件 I/O，必须排除在 _mock_env_config_in_ci 之外（mock 的
# set/delete 只操作 os.environ，会导致审计文件永不创建、.env 内容为空）。
_MOCK_ENV_CONFIG_EXCLUDED_FILES = (
    'test_env_config_audit.py',
    'test_env_hot_reload.py',
    'test_env_file_permissions.py',
)


@pytest.fixture(scope="function", autouse=True)
def _mock_env_config_in_ci(request):
    """CI 环境中 mock EnvConfigManager.set/delete，绕过 .env 文件 I/O。

    【不易】不改变测试断言语义——仍验证 NetworkConfigManager 正确调用
           _save_secure 并传递正确的 key/value 到 EnvConfigManager。
    【变易】仅 SKILLS_OFFLINE=1（CI 环境）激活，本地开发走真实 .env 写入；
           全局 autouse 对非 CI 环境为 no-op（早返回），零副作用。
           【CHG-2026-0801】黑名单排除验证 EnvConfigManager 自身文件写入/
           审计日志/权限契约的测试（见 _MOCK_ENV_CONFIG_EXCLUDED_FILES），
           否则 mock 的 set/delete 会破坏其断言（CI 上 13+8+4=25 个失败）。
    【简易】mock 直接操作 os.environ，无文件 I/O。

    Why 集中到 conftest.py: 原 test_network_config.py 与
         test_network_config_save_regression.py 各自定义了完全相同的 fixture，
         违反 DRY。提取后两个测试文件零侵入复用，新增同类测试亦自动继承。
    Why autouse=True 安全: SKILLS_OFFLINE 守卫保证仅 CI 环境激活 mock；
         CI 中所有测试都依赖 EnvConfigManager 写入，mock 后改走 os.environ，
         断言语义不变（仍校验 key/value 正确传递）。
    """
    if not os.environ.get('SKILLS_OFFLINE'):
        yield
        return

    # 【不易】黑名单排除：验证 EnvConfigManager 自身文件写入/审计日志/权限
    # 契约的测试需要真实 I/O，全局 mock 会破坏断言，必须放行。
    if any(f in request.node.nodeid for f in _MOCK_ENV_CONFIG_EXCLUDED_FILES):
        yield
        return

    from unittest.mock import patch
    from agent.env_config_manager import EnvConfigManager

    def _mock_set(self, key, value):
        """绕过 .env 文件写入，直接设置 os.environ（热重载等效）"""
        os.environ[key] = value

    def _mock_delete(self, key):
        """绕过 .env 文件删除，直接移除 os.environ"""
        os.environ.pop(key, None)

    with patch.object(EnvConfigManager, 'set', _mock_set), \
         patch.object(EnvConfigManager, 'delete', _mock_delete):
        yield


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """设置测试环境 - 会话级别自动执行"""
    print(f"\n{'='*60}")
    print(f"开始测试会话 - 环境: {TEST_CONFIG['env']}")
    print(f"测试报告目录: {TEST_CONFIG['report_dir']}")
    print(f"{'='*60}\n")

    yield

    print(f"\n{'='*60}")
    print(f"测试会话结束")
    print(f"{'='*60}\n")

@pytest.fixture(scope="function", autouse=True)
def reset_environment():
    """每个测试函数前后重置环境"""
    # 测试前
    original_cwd = os.getcwd()
    original_env = os.environ.copy()

    yield

    # 测试后清理
    os.chdir(original_cwd)
    os.environ.clear()
    os.environ.update(original_env)


# ════════════════════════════════════════════════════════════
# 测试污染强制清理 helpers（黄金快照 + 强制恢复）
# ════════════════════════════════════════════════════════════
# Why: 完整套件下（pytest-randomly 随机顺序）失败集随种子漂移（默认 31 / seed=12345 28），
# 核心机制是全局状态/类静态方法被前序测试 patch 泄漏或修改。与其逐个定位污染源，
# 采用「conftest 加载时快照真实引用 → 每测试后若发现被替换为 Mock 则强制恢复」兜底。
# 验证：9 个"看似恒定"失败（boundary 5 + message_handler 2 + singleton 2）单独运行
# 全部通过（9 passed in 2.15s）→ 均为污染受害方而非真实缺陷。

_GOLDEN_METHODS = {}


def _snapshot_golden_methods():
    """conftest 加载时（早于任何测试）快照易被 patch 泄漏的类静态方法真实引用。

    patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up", ...)
    若在异常/嵌套场景下未恢复，后续测试读到 MagicMock（name='is_follow_up'），
    assert False 失败。快照取真实函数引用，恢复时 setattr 回去即可。
    """
    global _GOLDEN_METHODS
    try:
        from agent.orchestrator.message_handler import MessageHandler
        for _m in ("parse", "is_simple_query", "detect_dissatisfaction",
                   "is_follow_up", "extract_keywords"):
            _GOLDEN_METHODS[("MessageHandler", _m)] = getattr(MessageHandler, _m)
    except Exception:
        pass  # 模块暂不可导入时跳过，fixture 内再延迟快照


def _force_restore_golden_methods():
    """若类静态方法被 mock 泄漏替换为 MagicMock，强制恢复为真实引用。"""
    for (_cls, _m), _orig in list(_GOLDEN_METHODS.items()):
        try:
            import agent.orchestrator.message_handler as _mod
            _cur = getattr(_mod.MessageHandler, _m)
            if isinstance(_cur, Mock):
                setattr(_mod.MessageHandler, _m, _orig)
        except Exception:
            pass


def _force_reset_intent_rules():
    """强制重置 IntentRouter._rules 为全新深拷贝默认规则，并恢复被 mock 泄漏的 classify/for_intent。

    Why deepcopy 而非 list()：list(_DEFAULT_RULES) 是浅拷贝，若前序测试
    修改了规则对象的 patterns（append 正则），浅拷贝仍携带污染；深拷贝
    保证每个测试拿到与源码完全一致的 8 条规则（意图全识别为 unknown 的根因）。

    Why 同时恢复静态方法：子线程内 `with patch(...)` 的 start/stop 竞态会把
    IntentRouter.classify 泄漏为 MagicMock（return_value=("unknown", ...)），
    classify 恒返回 unknown（response_workflows 17 失败）；仅重置 _rules 无法恢复，
    必须把类属性回写为模块加载时的真实函数。
    """
    try:
        from agent import response_workflows as _rw
        _rw.IntentRouter._rules = copy.deepcopy(_rw._DEFAULT_RULES)
    except Exception:
        pass
    # 懒初始化 golden 静态方法引用（首次调用时快照，避免循环导入）
    _f = _force_reset_intent_rules
    if not hasattr(_f, "_golden_classify"):
        try:
            from agent.response_workflows import IntentRouter as _IR, ResponseTemplates as _RT
            _f._golden_classify = _IR.classify
            _f._golden_for_intent = _RT.for_intent
        except Exception:
            return
    try:
        from agent.response_workflows import IntentRouter as _IR, ResponseTemplates as _RT
        if isinstance(getattr(_IR, "classify", None), Mock):
            setattr(_IR, "classify", staticmethod(_f._golden_classify))
        if isinstance(getattr(_RT, "for_intent", None), Mock):
            setattr(_RT, "for_intent", staticmethod(_f._golden_for_intent))
    except Exception:
        pass


def _force_reset_scheduler_singleton():
    """若 task_scheduler._scheduler 被 patch 泄漏为 MagicMock，置 None 触发重建。

    Why: test_task_scheduler_integration.py L937 `patch("agent.task_scheduler._scheduler")`
    不带 new 参数时会替换为 MagicMock；若未恢复，get_scheduler() 读到非 None 的
    Mock → isinstance(_, TaskScheduler) 断言失败（test_get_scheduler_returns_instance）。
    """
    try:
        import agent.task_scheduler as _ts
        if isinstance(getattr(_ts, "_scheduler", None), Mock):
            _ts._scheduler = None
    except Exception:
        pass


_snapshot_golden_methods()


# ════════════════════════════════════════════════════════════
# 跨平台临时目录清理兜底（chroma sqlite 句柄占用）
# ════════════════════════════════════════════════════════════
# Why: seed=12345 下 memory_module 18 个失败，traceback 定位在 shutil.rmtree
# （TemporaryDirectory.cleanup 阶段）：
#   PermissionError [WinError 32] 文件被占用 → 链式 NotADirectoryError [WinError 267]
# 根因：chromadb PersistentClient 的 sqlite 连接在测试结束时不释放文件句柄，
# Windows 无法删除被占用文件（POSIX 允许删除打开中的文件，故仅 Windows 受影响）。

# 模块加载时保存原始类：_safe_tmp_directory 会把 tempfile.TemporaryDirectory
# 替换为 _RetryTemporaryDirectory，内部创建必须引用此原始类（防无限递归）
_ORIG_TEMPFILE_TEMPDIR_CLS = tempfile.TemporaryDirectory


class _RetryTemporaryDirectory:
    """跨平台安全的临时目录：Windows 上 cleanup 重试 + 最终忽略而非抛错。

    与 tempfile.TemporaryDirectory 接口兼容（with 模式），替换后对现有测试
    零侵入——test_memory_module 等均以 `with tempfile.TemporaryDirectory() as d:`
    方式使用，__enter__ 返回路径字符串、__exit__ 兜底清理。
    """

    def __init__(self, suffix=None, prefix=None, dir=None,
                 *, ignore_cleanup_errors=False):
        # 必须用模块加载时保存的原始类，而非 tempfile.TemporaryDirectory——
        # 后者在 _safe_tmp_directory 运行期间已被替换为本类，直接引用会无限递归
        self._inner = _ORIG_TEMPFILE_TEMPDIR_CLS(
            suffix=suffix, prefix=prefix, dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
        )

    @property
    def name(self) -> str:
        return self._inner.name

    def cleanup(self) -> None:
        self._try_cleanup()

    def __enter__(self) -> str:
        return self._inner.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._try_cleanup()
        return False

    def _try_cleanup(self) -> None:
        if sys.platform != "win32":
            # POSIX：删除打开中的文件合法，直接清理
            self._inner.cleanup()
            return
        # Windows：chroma sqlite/segment 句柄可能延迟释放，短暂重试等待。
        # 捕获 OSError（PermissionError 文件占用 / rmtree 重试后的半删状态
        # NotADirectoryError），避免链式抛错。
        for _ in range(5):
            try:
                self._inner.cleanup()
                return
            except OSError:
                time.sleep(0.3)
        # 最终仍失败：保留目录并告警，绝不阻断测试、
        # 绝不让 rmtree 半删状态链式抛出 NotADirectoryError
        logging.getLogger("pytest").warning(
            "[_RetryTemporaryDirectory] 临时目录清理失败，已保留: %s",
            self._inner.name,
        )


@pytest.fixture(scope="session", autouse=True)
def _safe_tmp_directory():
    """跨平台临时目录兜底（session 级，autouse）。

    1. tempfile.tempdir 重定向到项目内 `.pytest_tmp`：
       - 跨平台路径一致、可诊断（不再依赖 C:\\Windows\\TEMP）
       - 避免系统 TEMP 的外部清理器/权限竞态干扰
    2. 替换 tempfile.TemporaryDirectory 为 _RetryTemporaryDirectory：
       - Windows 上 chroma sqlite 句柄占用时重试清理，最终保留+告警
    """
    _orig_dir = tempfile.tempdir
    _orig_cls = tempfile.TemporaryDirectory
    _base = PROJECT_ROOT / ".pytest_tmp"
    _base.mkdir(exist_ok=True)
    tempfile.tempdir = str(_base)
    tempfile.TemporaryDirectory = _RetryTemporaryDirectory
    yield
    tempfile.tempdir = _orig_dir
    tempfile.TemporaryDirectory = _orig_cls


@pytest.fixture(scope="function", autouse=True)
def reset_global_singletons():
    """每个测试后清理模块级全局单例与 ContextVar，防止测试间状态污染。

    Why: error_handler/metrics/state_manager/tracing 均为模块级单例，其内部
    计数器、字典、注册表会在测试间累积；circuit_breaker/disaster_recovery/
    graceful_degrade 的 _trace_id_ctx ContextVar 也会泄漏 trace_id。
    除 tracing 为懒加载单例外，其余 getter 不重建实例，故采用「清空实例内部
    状态容器」策略以保持实例引用稳定（避免 session-scope fixture 持有的旧
    引用失效）。
    另: setup_agent_logging() 会清除 root logger 的 handler 并添加带
    EmojiFilter/SensitiveDataFilter 的 handler，若不恢复会导致后续测试的
    emoji 被替换为 [ROCKET] 等、audit 模块日志被过滤。故在 yield 前快照
    root logger 的 handlers/level，yield 后恢复。
    """
    # 0. 快照 root logger 状态（防止 setup_agent_logging 污染）
    _root_logger = logging.getLogger()
    _saved_handlers = _root_logger.handlers[:]
    _saved_level = _root_logger.level
    yield
    # 0. 恢复 root logger 状态
    _root_logger.handlers = _saved_handlers
    _root_logger.setLevel(_saved_level)
    # 0b. 重置 logging 进程级全局开关 Manager.disable（防泄漏兜底）
    # Why: logging.disable(level) 设置 Manager.disable 后，任何 logger 的
    # isEnabledFor 对低于该 level 的记录恒 False（进程级屏蔽，conftest 的
    # 快照/恢复均不覆盖此属性）。若某测试因断言失败未恢复，后续所有依赖
    # INFO 级日志 filter 链的测试静默失败（perf_monitor 7 失败根因：
    # stress_test 的 logger.info() 静默丢弃记录 → 注入 filter 永不触发、
    # errors=0 与断言矛盾）。此处强制复位 NOTSET，阻断同类泄漏。
    logging.root.manager.disable = logging.NOTSET
    # 1. ErrorHandler: 清空错误计数与熔断器注册表
    try:
        from agent.error_handler import get_error_handler
        _inst = get_error_handler()
        _inst._metrics.clear()
        _inst._circuit_breakers.clear()
    except Exception:
        pass
    # 2. MetricsCollector: 清空 histogram 与 counter
    try:
        from agent.monitoring.metrics import get_metrics_collector
        _inst = get_metrics_collector()
        _inst._histograms.clear()
        _inst._counters.clear()
    except Exception:
        pass
    # 3. ServerState: __init__ 无副作用，重新初始化实例属性（不替换实例）
    try:
        from agent.state_manager import get_server_state
        get_server_state().__init__()
    except Exception:
        pass
    # 4. TraceStorage: 懒加载单例，置 None 触发下次访问重建
    try:
        import agent.monitoring.tracing as _tr
        _tr.reset_trace_storage()
    except Exception:
        pass
    # 5. ContextVar 重置：circuit_breaker / disaster_recovery / graceful_degrade / tracing
    # Why: tracing._current_trace_id 若被前序测试 set_trace_id() 设置且未恢复，
    # TraceContext.__enter__ 会复用旧值而非生成新 ID，导致唯一性/长度断言失败
    for _mod_path, _var_name in (
        ("agent.circuit_breaker", "_trace_id_ctx"),
        ("agent.disaster_recovery", "_trace_id_ctx"),
        ("agent.graceful_degrade", "_trace_id_ctx"),
        ("agent.monitoring.tracing", "_current_trace_id"),
        ("agent.monitoring.tracing", "_current_span_id"),
    ):
        try:
            _mod = __import__(_mod_path, fromlist=[_var_name])
            _var = getattr(_mod, _var_name, None)
            if _var is not None:
                _var.set(None)
        except Exception:
            pass
    # 6. CircuitBreaker: 清空所有命名熔断器实例（如 schema_validation）
    # Why: OutputSchemaValidator.parse_and_validate 通过 get_circuit_breaker("schema_validation")
    # 获取命名熔断器，前序测试的 record_failure 累积会导致熔断器打开，后续测试进入降级路径
    try:
        from agent.circuit_breaker import reset_breakers
        reset_breakers()
    except Exception:
        pass
    # 7. GracefulDegrade: 重置降级管理器单例状态（_states/_metrics/_module_states）
    # Why: 前序测试触发错误会累积 _states 中的错误计数，导致降级级别升至 LENIENT，
    # 使 OutputSchemaValidator 返回 degraded_lenient 响应而非 ErrorMessage
    try:
        from agent.graceful_degrade import reset_degrade_manager
        reset_degrade_manager()
    except Exception:
        pass
    # 8. browser_tools: 保留原有清理（防止真实 Chrome 实例泄漏）
    try:
        import agent.tools.browser_tools as _bt
        _bt._browser_instance = None
    except Exception:
        pass
    # 9. error_reporting_config: 重置敏感字段模式列表
    # Why: set_sensitive_patterns 会覆盖默认 _sensitive_patterns，若前序测试未恢复，
    # 会导致 Authorization 等敏感 key 不被识别，后续测试脱敏行为异常
    # 注: 只重置 _sensitive_patterns，不调用 _reset_for_test() 以避免影响 _sentry_initialized
    try:
        import agent.error_reporting_config as _erc
        _erc._sensitive_patterns = list(_erc._DEFAULT_SENSITIVE_PATTERNS)
    except Exception:
        pass
    # 10. system_prompt_config: 重置 _manager 单例
    # Why: 前序测试调用 get_manager() 创建单例并缓存配置,可能导致后续测试
    # 的配置查询读到陈旧缓存。重置确保每个测试拿到干净的配置管理器。
    try:
        import agent.system_prompt_config as _spc
        _spc.reset_system_prompt_manager()
    except Exception:
        pass
    # 11. sqlite_vec: 清理 sys.modules 中所有 sqlite_vec 相关键
    # Why: test_vector_store_sqlite_vec.py 的 _enable_sqlite_vec_for_tests 用
    # patch.dict(sys.modules, ...) 覆盖 _BlockModules 封禁，其 __exit__ 会
    # _clear_dict 清空测试期间新导入的模块键（如 sqlite_vec.util）但父包属性
    # 仍引用旧模块对象，形成 sys.modules 与包属性不一致的残留。删除所有
    # sqlite_vec* 键，强制后续测试全新导入（或被 _BlockModules 封禁），
    # 避免 C 扩展重复加载/引用残留导致偶发 ERROR。
    try:
        import sys as _sys
        for _key in [k for k in list(_sys.modules) if k == "sqlite_vec" or k.startswith("sqlite_vec.")]:
            _sys.modules.pop(_key, None)
    except Exception:
        pass
    # 12. memory.vector_store: 清空共享编码器单例缓存
    # Why: VectorStore._get_shared_encoder（vector_store.py）是模块级单例缓存，
    # 前序测试若在 sentence_transformers 被 mock（MagicMock 模块）的上下文中
    # 实例化 VectorStore，会把 mock 编码器缓存进 _shared_encoder_cache。后续
    # sqlite-vec 测试即使 patch 了 SentenceTransformer，_get_shared_encoder 仍
    # 命中缓存返回 mock 编码器，get_sentence_embedding_dimension() 得到 MagicMock，
    # vec0 DDL 构造失败降级 json → "expected sqlite_vec, got json"（随机序
    # TestVectorStoreSqliteVecIntegration 8 ERROR + backend 1 FAILED 根因）。
    try:
        import memory.vector_store.vector_store as _vstore
        _vstore._shared_encoder_cache.clear()
        # 12b. 移除被 mock 污染的 sentence_transformers 模块
        # Why: test_reranker.py 模块级 `sys.modules["sentence_transformers"] =
        # MagicMock()`（真实模块未导入时设置）会永久残留；VectorStore 从 Mock
        # 模块取 SentenceTransformer 类（可调用不抛异常）→ 缓存 Mock 编码器 →
        # memory 全链路 add 失败（assert 0 == N，seed 顺序下 11 个失败根因）。
        # 移除后强制后续重新导入真实模块；真实模块不可导入时 _get_shared_encoder
        # 返回 None（不缓存），VectorStore 正确降级 JSON。
        import sys as _sys
        _st_mod = _sys.modules.get("sentence_transformers")
        if _st_mod is not None and hasattr(_st_mod, "mock_calls"):
            _sys.modules.pop("sentence_transformers", None)
        # 注意：不要清理被 mock 污染的 transformers。Why: 真实 import transformers
        # 会加载 torch C 扩展 → Windows 0xC0000005 崩溃（reranker 测试模块级 mock
        # 三件套正是防崩溃屏障）。transformers 的 MagicMock 残留阻止任何后续真实
        # import（import 链返回 Mock，不加载 torch）→ 安全失败模式。
    except Exception:
        pass
    # 13. 强制恢复被 patch 泄漏的类静态方法（MessageHandler 5 个）
    # Why: e2e 测试大量 patch("agent.orchestrator.message_handler.MessageHandler.*")，
    # 泄漏后 MagicMock 覆盖真实实现 → test_message_handler / test_orchestrator_boundary
    # 的 is_follow_up/detect_dissatisfaction/extract_keywords 断言失败。
    _force_restore_golden_methods()
    # 14. 强制重置 IntentRouter._rules（deepcopy 默认规则）
    # Why: 意图规则注册表被清空/污染后 classify 全部返回 unknown（response_workflows 17 失败）。
    _force_reset_intent_rules()
    # 15. 强制重置 task_scheduler 单例（Mock 泄漏时置 None 重建）
    # Why: patch("agent.task_scheduler._scheduler") 泄漏 → get_scheduler() 返回 Mock。
    _force_reset_scheduler_singleton()

# ============================================================================
# 测试断言辅助函数
# ============================================================================

def assert_response_success(response: Dict[str, Any], msg: str = ""):
    """断言响应成功"""
    assert response.get("success", False), f"响应失败: {msg}, 响应: {response}"

def assert_error_type(error: Exception, expected_type: type, msg: str = ""):
    """断言错误类型"""
    assert isinstance(error, expected_type), \
        f"{msg} 期望错误类型: {expected_type}, 实际: {type(error)}"

def assert_metrics_threshold(metrics: Dict[str, float], thresholds: Dict[str, float]):
    """断言指标在阈值范围内"""
    for key, threshold in thresholds.items():
        value = metrics.get(key)
        if value is not None:
            assert value <= threshold, \
                f"指标 {key} 超标: {value} > {threshold}"

# ============================================================================
# 测试跳过条件（逻辑已合并到上方的 pytest_collection_modifyitems 中）
# ============================================================================

def pytest_addoption(parser):
    """添加命令行选项"""
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="运行慢速测试"
    )
    parser.addoption(
        "--env",
        action="store",
        default="development",
        help="指定测试环境"
    )
    parser.addoption(
        "--report-format",
        action="store",
        default="html",
        choices=["html", "json", "xml"],
        help="测试报告格式"
    )

# ============================================================================
# pytest钩子 - 测试结果收集
# ============================================================================

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """测试结束后的终端摘要"""
    if exitstatus == 0:
        terminalreporter.write_sep("=", "所有测试通过！✓", green=True, bold=True)
    else:
        terminalreporter.write_sep("=", "测试失败 - 需要修复！✗", red=True, bold=True)

    # 输出关键统计
    stats = terminalreporter.stats
    terminalreporter.write_line("\n测试统计:")
    terminalreporter.write_line(f"  通过: {len(stats.get('passed', []))}")
    terminalreporter.write_line(f"  失败: {len(stats.get('failed', []))}")
    terminalreporter.write_line(f"  跳过: {len(stats.get('skipped', []))}")

# ============================================================================
# 导出公共API
# ============================================================================

__all__ = [
    "TestDataManager",
    "assert_response_success",
    "assert_error_type",
    "assert_metrics_threshold",
]
