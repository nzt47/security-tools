"""cognitive 模块边界测试 — BT-008

补充 cognitive 模块缺失的 timeout 场景。
cognitive 模块全为同步纯计算，无天然 timeout 语义，
通过性能/耗时测试覆盖 timeout 边界场景。

被测模块：cognitive/（项目根目录的 cognitive/ 包）
关键 API：
- PromptInjector: inject/translate/get_summary/should_reject_task
- Translator: translate/translate_all/get_status_line
- TemplateManager: render

【可观测性约束】
- 边界显性化：每个边界条件显式断言
"""

import logging
import time

import pytest

# cognitive 在项目根目录
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cognitive.prompt_injector import PromptInjector
from cognitive.translator import Translator
from cognitive.templates import TemplateManager
from cognitive.config import PromptConfig


logger = logging.getLogger(__name__)


# ============================================================================
#  fixtures
# ============================================================================


@pytest.fixture
def injector():
    """PromptInjector 实例"""
    return PromptInjector()


@pytest.fixture
def translator():
    """Translator 实例"""
    return Translator(PromptConfig())


@pytest.fixture
def template_mgr():
    """TemplateManager 实例"""
    return TemplateManager()


@pytest.fixture
def large_sensor_data():
    """大量传感器数据"""
    return [
        {"name": f"sensor_{i}", "value": i, "severity": "normal"}
        for i in range(1000)
    ]


@pytest.fixture
def critical_sensor_data():
    """包含 critical 级别的传感器数据"""
    return [
        {"name": "cpu", "value": 95, "severity": "critical"},
        {"name": "memory", "value": 88, "severity": "warning"},
        {"name": "disk", "value": 50, "severity": "normal"},
    ]


# ============================================================================
#  timeout 边界场景测试
# ============================================================================


class TestTimeoutBoundary:
    """超时/性能边界测试 — cognitive 模块无天然 timeout，通过耗时上限覆盖"""

    def test_timeout_inject_empty_data(self, injector):
        """空数据注入应在合理时间内完成"""
        start = time.time()
        result = injector.inject([])
        duration = time.time() - start
        assert isinstance(result, str)
        assert duration < 1.0  # 空数据应 < 1 秒

    def test_timeout_inject_large_data(self, injector, large_sensor_data):
        """大量数据注入应在合理时间内完成"""
        start = time.time()
        result = injector.inject(large_sensor_data)
        duration = time.time() - start
        assert isinstance(result, str)
        assert duration < 5.0  # 1000 条数据应 < 5 秒

    def test_timeout_inject_none_data(self, injector):
        """None 数据注入应在合理时间内完成"""
        start = time.time()
        result = injector.inject(None)
        duration = time.time() - start
        assert isinstance(result, str)
        assert duration < 1.0

    def test_timeout_translate_all_large_batch(self, translator, large_sensor_data):
        """大批量翻译应在合理时间内完成"""
        start = time.time()
        results = translator.translate_all(large_sensor_data)
        duration = time.time() - start
        assert isinstance(results, list)
        assert duration < 5.0

    def test_timeout_get_summary_large_data(self, injector, large_sensor_data):
        """大量数据获取摘要应在合理时间内完成"""
        start = time.time()
        result = injector.get_summary(large_sensor_data)
        duration = time.time() - start
        assert isinstance(result, str)
        assert duration < 5.0

    def test_timeout_should_reject_task_large_data(self, injector, large_sensor_data):
        """大量数据任务拒绝判断应在合理时间内完成"""
        start = time.time()
        should_reject, reason = injector.should_reject_task(large_sensor_data)
        duration = time.time() - start
        assert isinstance(should_reject, bool)
        assert isinstance(reason, str)
        assert duration < 5.0

    def test_timeout_render_large_template(self, template_mgr):
        """大模板渲染应在合理时间内完成"""
        large_body = "x" * 10000
        start = time.time()
        result = template_mgr.render("default", body_status=large_body, task_guidance="test")
        duration = time.time() - start
        assert isinstance(result, str)
        assert duration < 1.0

    def test_timeout_repeated_inject(self, injector, critical_sensor_data):
        """重复注入应在合理时间内完成"""
        start = time.time()
        for _ in range(100):
            injector.inject(critical_sensor_data)
        duration = time.time() - start
        assert duration < 10.0  # 100 次注入应 < 10 秒


# ============================================================================
#  empty/invalid/null 补充测试（已有覆盖，此处补充边界）
# ============================================================================


class TestEmptyBoundary:
    """空值边界测试"""

    def test_empty_sensor_data_inject(self, injector):
        """空列表传感器数据注入"""
        result = injector.inject([])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_sensor_data_get_summary(self, injector):
        """空列表获取摘要"""
        result = injector.get_summary([])
        assert isinstance(result, str)

    def test_empty_sensor_data_should_reject(self, injector):
        """空列表任务拒绝判断"""
        should_reject, reason = injector.should_reject_task([])
        assert should_reject is False


class TestInvalidInput:
    """非法输入测试"""

    def test_invalid_sensor_data_not_list(self, injector):
        """sensor_data 非列表"""
        result = injector.inject("not_a_list")
        assert isinstance(result, str)

    def test_invalid_sensor_data_int(self, injector):
        """sensor_data 为整数"""
        result = injector.inject(12345)
        assert isinstance(result, str)

    def test_invalid_sensor_items_not_dict(self, injector):
        """sensor_data 包含非字典项"""
        result = injector.inject([1, 2, "string", None, {"name": "valid"}])
        assert isinstance(result, str)


class TestNullBoundary:
    """None 值处理测试"""

    def test_null_sensor_data_inject(self, injector):
        """None 传感器数据注入"""
        result = injector.inject(None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_null_sensor_data_get_summary(self, injector):
        """None 获取摘要 — get_status_line 迭代 None 抛 TypeError"""
        with pytest.raises(TypeError):
            injector.get_summary(None)

    def test_null_sensor_data_should_reject(self, injector):
        """None 任务拒绝判断"""
        with pytest.raises((AttributeError, TypeError)):
            injector.should_reject_task(None)


# ============================================================================
#  extreme 极值测试
# ============================================================================


class TestExtremeValues:
    """极值测试"""

    def test_extreme_very_large_sensor_value(self, injector):
        """超大传感器数值"""
        data = [{"name": "cpu", "value": 10**10, "severity": "critical"}]
        result = injector.inject(data)
        assert isinstance(result, str)

    def test_extreme_negative_sensor_value(self, injector):
        """负数传感器数值"""
        data = [{"name": "temp", "value": -100, "severity": "normal"}]
        result = injector.inject(data)
        assert isinstance(result, str)

    def test_extreme_many_critical_sensors(self, injector):
        """大量 critical 传感器"""
        data = [{"name": f"sensor_{i}", "value": 99, "severity": "critical"} for i in range(100)]
        should_reject, _ = injector.should_reject_task(data)
        assert should_reject is True

    def test_extreme_long_sensor_name(self, injector):
        """超长传感器名称"""
        data = [{"name": "x" * 10000, "value": 50, "severity": "normal"}]
        result = injector.inject(data)
        assert isinstance(result, str)
