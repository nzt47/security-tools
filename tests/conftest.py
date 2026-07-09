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
import pytest

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
        _tr._trace_storage_singleton = None
    except Exception:
        pass
    # 5. ContextVar 重置：circuit_breaker / disaster_recovery / graceful_degrade
    for _mod_path, _var_name in (
        ("agent.circuit_breaker", "_trace_id_ctx"),
        ("agent.disaster_recovery", "_trace_id_ctx"),
        ("agent.graceful_degrade", "_trace_id_ctx"),
    ):
        try:
            _mod = __import__(_mod_path, fromlist=[_var_name])
            _var = getattr(_mod, _var_name, None)
            if _var is not None:
                _var.set("")
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
