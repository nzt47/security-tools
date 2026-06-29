"""Orchestrator 核心模块重构单元测试

覆盖模块：
- response_builder.py: 8个静态工厂方法
- lifecycle_manager.py: 初始化流程、模块可用性检查、V2功能配置
- orchestrator.py: 行为模式获取、上下文使用率检查、关键词提取
- status_reporter.py: 状态报告生成、健康检查
- subagent_manager.py: 子代理创建、销毁、列表管理
- task_dispatcher.py: 智能工具选择开关、额外配置节开关
"""
import pytest
import sys
import builtins
from unittest.mock import Mock, patch, MagicMock, PropertyMock
from dataclasses import dataclass
from typing import Any as _Any, Optional

# 修复 subagent_manager 模块缺少 Any 导入的问题
# 在导入前将 Any 注入到 builtins，确保类定义时能找到
builtins.Any = _Any

import agent.orchestrator.subagent_manager  # noqa: E402


# ============================================================================
#  ResponseBuilder 测试
# ============================================================================

class TestResponseBuilder:
    """ResponseBuilder 静态工厂方法测试"""

    def test_response_builder_success_正常路径(self):
        """测试 success 工厂方法构建成功响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.success(data={"key": "value"}, msg="操作成功")
        assert r.success is True
        assert r.data == {"key": "value"}
        assert r.msg == "操作成功"
        assert r.error is None
        assert r.metadata == {}

    def test_response_builder_success_默认参数(self):
        """测试 success 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.success()
        assert r.success is True
        assert r.data is None
        assert r.msg == "ok"
        assert r.error is None

    def test_response_builder_error_正常路径(self):
        """测试 error 工厂方法构建错误响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.error(error="数据库连接失败", msg="操作失败")
        assert r.success is False
        assert r.error == "数据库连接失败"
        assert r.msg == "操作失败"
        assert r.data is None

    def test_response_builder_error_默认参数(self):
        """测试 error 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.error()
        assert r.success is False
        assert r.error == ""
        assert r.msg == "error"

    def test_response_builder_rejection_正常路径(self):
        """测试 rejection 工厂方法构建拒绝响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.rejection(reason="权限不足", mode="strict")
        assert r.success is False
        assert r.error == "权限不足"
        assert r.msg == "rejected"
        assert r.metadata["mode"] == "strict"

    def test_response_builder_rejection_默认参数(self):
        """测试 rejection 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.rejection()
        assert r.success is False
        assert r.error == ""
        assert r.msg == "rejected"
        assert r.metadata["mode"] == ""

    def test_response_builder_guard_blocked_正常路径(self):
        """测试 guard_blocked 工厂方法构建安全护栏拦截响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.guard_blocked(reason="SQL注入检测", pattern="sql_injection")
        assert r.success is False
        assert "安全护栏拦截" in r.error
        assert "SQL注入检测" in r.error
        assert r.msg == "blocked_by_guard"
        assert r.metadata["matched_pattern"] == "sql_injection"

    def test_response_builder_guard_blocked_默认参数(self):
        """测试 guard_blocked 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.guard_blocked()
        assert r.success is False
        assert r.msg == "blocked_by_guard"
        assert r.metadata["matched_pattern"] == ""

    def test_response_builder_workflow_result_正常路径(self):
        """测试 workflow_result 工厂方法构建工作流结果响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.workflow_result(
            output="查询结果：共10条记录",
            intent="search_query",
            confidence=0.95
        )
        assert r.success is True
        assert r.data["output"] == "查询结果：共10条记录"
        assert r.data["intent"] == "search_query"
        assert r.data["confidence"] == 0.95
        assert r.msg == "handled_by_workflow"

    def test_response_builder_workflow_result_默认参数(self):
        """测试 workflow_result 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.workflow_result()
        assert r.success is True
        assert r.data["output"] == ""
        assert r.data["intent"] == ""
        assert r.data["confidence"] == 1.0
        assert r.msg == "handled_by_workflow"

    def test_response_builder_llm_result_正常路径(self):
        """测试 llm_result 工厂方法构建 LLM 调用结果响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.llm_result(text="你好，我是云枢", model="claude-4-opus")
        assert r.success is True
        assert r.data["text"] == "你好，我是云枢"
        assert r.data["model"] == "claude-4-opus"
        assert r.msg == "llm_response"

    def test_response_builder_llm_result_默认参数(self):
        """测试 llm_result 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.llm_result()
        assert r.success is True
        assert r.data["text"] == ""
        assert r.data["model"] == ""
        assert r.msg == "llm_response"

    def test_response_builder_offline_正常路径(self):
        """测试 offline 工厂方法构建离线模式响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.offline(reason="网络不可用")
        assert r.success is True
        assert "离线模式" in r.data["text"]
        assert "网络不可用" in r.data["text"]
        assert r.msg == "offline"

    def test_response_builder_offline_默认参数(self):
        """测试 offline 工厂方法默认参数行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        r = ResponseBuilder.offline()
        assert r.success is True
        assert "离线模式" in r.data["text"]
        assert r.msg == "offline"

    def test_response_builder_from_exception_正常路径(self):
        """测试 from_exception 工厂方法从异常构建错误响应"""
        from agent.orchestrator.response_builder import ResponseBuilder

        try:
            raise ValueError("参数校验失败")
        except ValueError as e:
            r = ResponseBuilder.from_exception(e, msg="validation_error")
            assert r.success is False
            assert r.error == "参数校验失败"
            assert r.msg == "validation_error"

    def test_response_builder_from_exception_默认消息(self):
        """测试 from_exception 工厂方法默认消息行为"""
        from agent.orchestrator.response_builder import ResponseBuilder

        try:
            raise RuntimeError("运行时错误")
        except RuntimeError as e:
            r = ResponseBuilder.from_exception(e)
            assert r.success is False
            assert r.error == "运行时错误"
            assert r.msg == "internal_error"


class TestResponseDataclass:
    """Response 数据类功能测试"""

    def test_response_to_dict_无错误时不包含error字段(self):
        """测试 to_dict 方法在成功响应中不包含 error 字段"""
        from agent.orchestrator.response_builder import Response

        r = Response(success=True, data=[1, 2, 3], msg="ok")
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"] == [1, 2, 3]
        assert d["msg"] == "ok"
        assert "error" not in d

    def test_response_to_dict_有错误时包含error字段(self):
        """测试 to_dict 方法在错误响应中包含 error 字段"""
        from agent.orchestrator.response_builder import Response

        r = Response(success=False, error="出错了", msg="fail")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "出错了"
        assert d["msg"] == "fail"

    def test_response_to_dict_包含metadata(self):
        """测试 to_dict 方法正确包含 metadata"""
        from agent.orchestrator.response_builder import Response

        r = Response(success=True, msg="ok", metadata={"version": "2.0", "trace_id": "abc123"})
        d = r.to_dict()
        assert d["metadata"] == {"version": "2.0", "trace_id": "abc123"}


# ============================================================================
#  LifecycleManager 测试
# ============================================================================

class TestLifecycleManagerModuleAvailability:
    """LifecycleManager 模块可用性检查测试"""

    @patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', False)
    def test_lifecycle_manager_check_module_availability_记录模块状态(self):
        """测试 _check_module_availability 方法正确记录各模块可用性状态"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, '__init__', lambda self, config=None: None):
            mgr = LifecycleManager()
            mgr._config = {}

            with patch('agent.orchestrator.lifecycle_manager.logger') as mock_logger:
                mgr._check_module_availability()

                assert mock_logger.info.called
                call_args = [str(call[0][0]) for call in mock_logger.info.call_args_list]
                assert any("模块可用性检查" in arg for arg in call_args)

    @patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', False)
    def test_lifecycle_manager_configure_v2_features_请求启用且模块可用(self):
        """测试 _configure_v2_features 在请求启用且模块可用时正确启用"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, '__init__', lambda self, config=None: None):
            mgr = LifecycleManager()
            mgr._config = {
                "features": {
                    "v2_lifetrace": True,
                    "v2_persona": True,
                    "v2_distillation": True,
                }
            }

            with patch('agent.orchestrator.lifecycle_manager.logger'):
                with patch('agent.system_prompt_config.is_section_enabled',
                           side_effect=ImportError()):
                    mgr._configure_v2_features()

                    assert mgr._v2_lifetrace is True
                    assert mgr._v2_persona is False
                    assert mgr._v2_distillation is False

    @patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', False)
    @patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', False)
    def test_lifecycle_manager_configure_v2_features_模块不可用时禁用(self):
        """测试 _configure_v2_features 在模块不可用时即使请求启用也禁用"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, '__init__', lambda self, config=None: None):
            mgr = LifecycleManager()
            mgr._config = {
                "features": {
                    "v2_lifetrace": True,
                    "v2_persona": True,
                    "v2_distillation": True,
                }
            }

            with patch('agent.orchestrator.lifecycle_manager.logger'):
                with patch('agent.system_prompt_config.is_section_enabled',
                           side_effect=ImportError()):
                    mgr._configure_v2_features()

                    assert mgr._v2_lifetrace is False
                    assert mgr._v2_persona is False
                    assert mgr._v2_distillation is False

    @patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', True)
    def test_lifecycle_manager_configure_v2_features_默认禁用(self):
        """测试 _configure_v2_features 在未请求时默认禁用所有 V2 功能"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, '__init__', lambda self, config=None: None):
            mgr = LifecycleManager()
            mgr._config = {}

            with patch('agent.orchestrator.lifecycle_manager.logger'):
                with patch('agent.system_prompt_config.is_section_enabled',
                           side_effect=ImportError()):
                    mgr._configure_v2_features()

                    assert mgr._v2_lifetrace is False
                    assert mgr._v2_persona is False
                    assert mgr._v2_distillation is False

    @patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', True)
    @patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', True)
    def test_lifecycle_manager_configure_v2_features_UI开关关闭时禁用(self):
        """测试 _configure_v2_features 在 UI 配置开关关闭时强制禁用功能"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, '__init__', lambda self, config=None: None):
            mgr = LifecycleManager()
            mgr._config = {
                "features": {
                    "v2_lifetrace": True,
                    "v2_persona": True,
                    "v2_distillation": True,
                }
            }

            def mock_is_section_enabled(section, default=True):
                if section == "lifetrace":
                    return False
                if section == "persona":
                    return False
                if section == "distillation":
                    return False
                return default

            with patch('agent.orchestrator.lifecycle_manager.logger'):
                with patch('agent.system_prompt_config.is_section_enabled',
                           side_effect=mock_is_section_enabled):
                    mgr._configure_v2_features()

                    assert mgr._v2_lifetrace is False
                    assert mgr._v2_persona is False
                    assert mgr._v2_distillation is False

    def test_lifecycle_manager_log_initialization_start_输出启动日志(self):
        """测试 _log_initialization_start 方法输出启动日志"""
        from agent.orchestrator.lifecycle_manager import LifecycleManager

        with patch.object(LifecycleManager, '__init__', lambda self, config=None: None):
            mgr = LifecycleManager()

            with patch('agent.orchestrator.lifecycle_manager.logger') as mock_logger:
                mgr._log_initialization_start()

                assert mock_logger.info.call_count >= 3
                call_args = [str(call[0][0]) for call in mock_logger.info.call_args_list]
                assert any("云枢初始化开始" in arg for arg in call_args)


# ============================================================================
#  Orchestrator 测试
# ============================================================================

class TestOrchestratorBehaviorMode:
    """Orchestrator 行为模式获取测试"""

    def test_orchestrator_get_behavior_mode_返回当前模式(self):
        """测试 get_behavior_mode 方法返回当前行为模式"""
        from agent.orchestrator.orchestrator import Orchestrator
        from agent.digital_life import BehaviorMode

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            orch._current_mode = BehaviorMode.NORMAL

            result = orch.get_behavior_mode()
            assert result == BehaviorMode.NORMAL

    def test_orchestrator_get_behavior_mode_不同模式(self):
        """测试 get_behavior_mode 在不同模式下的返回值"""
        from agent.orchestrator.orchestrator import Orchestrator
        from agent.digital_life import BehaviorMode

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()

            for mode in [BehaviorMode.NORMAL, BehaviorMode.SAFE, BehaviorMode.POWER_SAVE,
                         BehaviorMode.MEMORY_COMPACT, BehaviorMode.OFFLINE]:
                orch._current_mode = mode
                assert orch.get_behavior_mode() == mode


class TestOrchestratorContextUsage:
    """Orchestrator 上下文使用率检查测试"""

    def test_orchestrator_check_context_usage_无记忆时返回None(self):
        """测试 _check_context_usage 在记忆不可用时返回 None"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            orch._memory = None

            result = orch._check_context_usage()
            assert result is None

    def test_orchestrator_check_context_usage_低使用率返回None(self):
        """测试 _check_context_usage 在上下文使用率低时返回 None"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.return_value = [{"role": "user", "content": "你好"}]
            mock_memory._token_counter.count_messages.return_value = 100
            mock_memory.compress_rounds = 0
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is None

    def test_orchestrator_check_context_usage_60到80返回info(self):
        """测试 _check_context_usage 在使用率 60%-80% 时返回 info 级别"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.return_value = [{"role": "user", "content": "测试" * 100}]
            mock_memory._token_counter.count_messages.return_value = 90000
            mock_memory.compress_rounds = 0
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is not None
            assert result["level"] == "info"
            assert 60 <= result["pct"] <= 80

    def test_orchestrator_check_context_usage_80到95返回warning(self):
        """测试 _check_context_usage 在使用率 80%-95% 时返回 warning 级别"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.return_value = [{"role": "user", "content": "测试" * 200}]
            mock_memory._token_counter.count_messages.return_value = 110000
            mock_memory.compress_rounds = 0
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is not None
            assert result["level"] == "warning"
            assert 80 <= result["pct"] <= 95

    def test_orchestrator_check_context_usage_超过95返回critical(self):
        """测试 _check_context_usage 在使用率超过 95% 时返回 critical 级别"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.return_value = [{"role": "user", "content": "测试" * 300}]
            mock_memory._token_counter.count_messages.return_value = 128000
            mock_memory.compress_rounds = 0
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is not None
            assert result["level"] == "critical"
            assert result["pct"] >= 95

    def test_orchestrator_check_context_usage_压缩3次返回warning(self):
        """测试 _check_context_usage 在压缩 3 次时返回 warning 级别"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.return_value = [{"role": "user", "content": "你好"}]
            mock_memory._token_counter.count_messages.return_value = 1000
            mock_memory.compress_rounds = 3
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is not None
            assert result["level"] == "warning"
            assert result["compress_rounds"] == 3

    def test_orchestrator_check_context_usage_压缩5次以上返回critical(self):
        """测试 _check_context_usage 在压缩 5 次以上时返回 critical 级别"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.return_value = [{"role": "user", "content": "你好"}]
            mock_memory._token_counter.count_messages.return_value = 1000
            mock_memory.compress_rounds = 6
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is not None
            assert result["level"] == "critical"
            assert result["compress_rounds"] == 6

    def test_orchestrator_check_context_usage_异常时返回None(self):
        """测试 _check_context_usage 在异常时返回 None"""
        from agent.orchestrator.orchestrator import Orchestrator

        with patch.object(Orchestrator, '__init__', lambda self: None):
            orch = Orchestrator()
            mock_memory = Mock()
            mock_memory.get_context.side_effect = Exception("测试异常")
            orch._memory = mock_memory
            orch._memory_token_limit = 131072

            result = orch._check_context_usage()
            assert result is None


class TestOrchestratorKeywordExtraction:
    """Orchestrator 关键词提取测试"""

    def test_orchestrator_extract_keywords_中文短句(self):
        """测试 _extract_keywords 从中文短句中提取关键词"""
        from agent.orchestrator.orchestrator import Orchestrator

        result = Orchestrator._extract_keywords("查询天气")
        assert isinstance(result, list)
        assert len(result) > 0
        assert "查询天气" in result

    def test_orchestrator_extract_keywords_中文长句(self):
        """测试 _extract_keywords 从中文长句中提取关键词"""
        from agent.orchestrator.orchestrator import Orchestrator

        result = Orchestrator._extract_keywords("帮我分析一下这个项目的代码质量和性能瓶颈")
        assert isinstance(result, list)
        assert len(result) >= 1
        assert len(result) <= 3

    def test_orchestrator_extract_keywords_空字符串(self):
        """测试 _extract_keywords 对空字符串返回空列表"""
        from agent.orchestrator.orchestrator import Orchestrator

        result = Orchestrator._extract_keywords("")
        assert result == []

    def test_orchestrator_extract_keywords_特殊字符(self):
        """测试 _extract_keywords 对纯特殊字符返回空列表"""
        from agent.orchestrator.orchestrator import Orchestrator

        result = Orchestrator._extract_keywords("!!!@@@###$$$")
        assert result == []

    def test_orchestrator_extract_keywords_去重功能(self):
        """测试 _extract_keywords 关键词去重功能"""
        from agent.orchestrator.orchestrator import Orchestrator

        result = Orchestrator._extract_keywords("测试测试测试")
        assert isinstance(result, list)
        assert len(result) == len(set(result))

    def test_orchestrator_extract_keywords_限制数量(self):
        """测试 _extract_keywords 返回关键词数量不超过 3 个"""
        from agent.orchestrator.orchestrator import Orchestrator

        result = Orchestrator._extract_keywords("这是一个非常长的句子用来测试关键词提取功能是否正常工作")
        assert len(result) <= 3


# ============================================================================
#  StatusReporter 测试
# ============================================================================

class TestStatusReporter:
    """StatusReporter 状态报告与健康检查测试"""

    def _create_mock_orchestrator(self):
        """创建模拟的 Orchestrator 实例"""
        mock_orch = Mock()

        mock_reading = Mock()
        mock_reading.sensor_name = "cpu"
        mock_reading.value = 45.0
        mock_reading.unit = "%"
        mock_reading.severity = "normal"
        mock_reading.description = "CPU 使用率"

        mock_orch.body.collect_quick.return_value = [mock_reading]
        mock_orch.body.get_health_report.return_value = "CPU: 45% - 正常"

        mock_profile = Mock()
        mock_profile.label = "普通模式"
        mock_profile.description = "正常工作模式"
        mock_profile.can_accept_tasks = True
        mock_profile.enable_reflection = True
        mock_orch._behavior.profile = mock_profile

        mock_orch._current_mode = "normal"
        mock_orch._session_id = "20240101_120000"
        mock_orch._running = True
        mock_orch._interaction_count = 42
        mock_orch._reflection_history = []
        mock_orch._v2_lifetrace = False
        mock_orch._v2_persona = False
        mock_orch._v2_distillation = False
        mock_orch._subagent_mgr = None
        mock_orch._memory = None

        mock_orch._get_enabled_tools_whitelist.return_value = ["tool1", "tool2"]

        return mock_orch

    def test_status_reporter_check_health_正常路径(self):
        """测试 check_health 方法正常返回传感器读数"""
        from agent.orchestrator.status_reporter import StatusReporter

        mock_orch = self._create_mock_orchestrator()
        reporter = StatusReporter(mock_orch)

        readings = reporter.check_health()

        assert isinstance(readings, list)
        assert len(readings) == 1
        assert readings[0].sensor_name == "cpu"
        mock_orch.body.collect_quick.assert_called_once()

    def test_status_reporter_check_health_更新最后检查时间(self):
        """测试 check_health 方法更新最后健康检查时间"""
        from agent.orchestrator.status_reporter import StatusReporter
        import time

        mock_orch = self._create_mock_orchestrator()
        mock_orch._last_health_check = 0
        reporter = StatusReporter(mock_orch)

        before = time.time()
        reporter.check_health()
        after = time.time()

        assert before <= mock_orch._last_health_check <= after

    def test_status_reporter_check_health_更新行为模式(self):
        """测试 check_health 方法根据读数更新行为模式"""
        from agent.orchestrator.status_reporter import StatusReporter

        mock_orch = self._create_mock_orchestrator()
        mock_orch._behavior.evaluate.return_value = "focus"
        reporter = StatusReporter(mock_orch)

        reporter.check_health()

        mock_orch._behavior.evaluate.assert_called_once()
        assert mock_orch._current_mode == "focus"

    def test_status_reporter_get_status_返回完整状态报告(self):
        """测试 get_status 方法返回完整的状态报告字典"""
        from agent.orchestrator.status_reporter import StatusReporter

        mock_orch = self._create_mock_orchestrator()

        with patch('agent.tools.list_tools', return_value=[{"name": "tool1"}, {"name": "tool2"}]):
            mock_orch._is_smart_tool_selection_enabled.return_value = False

            reporter = StatusReporter(mock_orch)
            status = reporter.get_status()

            assert "云枢" in status
            assert "行为模式" in status
            assert "身体状态" in status
            assert "系统" in status
            assert "搜索引擎" in status
            assert "分身" in status

            assert status["云枢"]["运行中"] is True
            assert status["云枢"]["交互次数"] == 42
            assert status["行为模式"]["模式名称"] == "普通模式"
            assert "cpu" in status["身体状态"]

    def test_status_reporter_get_status_V2功能未启用时不包含(self):
        """测试 get_status 在 V2 功能未启用时不包含相关字段"""
        from agent.orchestrator.status_reporter import StatusReporter

        mock_orch = self._create_mock_orchestrator()
        mock_orch._v2_lifetrace = False
        mock_orch._v2_persona = False
        mock_orch._v2_distillation = False

        with patch('agent.tools.list_tools', return_value=[]):
            mock_orch._is_smart_tool_selection_enabled.return_value = False

            reporter = StatusReporter(mock_orch)
            status = reporter.get_status()

            assert "LifeTrace" not in status
            assert "Persona" not in status
            assert "人格蒸馏" not in status

    def test_status_reporter_get_status_text_返回可读状态(self):
        """测试 get_status_text 方法返回人类可读的状态文本"""
        from agent.orchestrator.status_reporter import StatusReporter

        mock_orch = self._create_mock_orchestrator()
        reporter = StatusReporter(mock_orch)

        text = reporter.get_status_text()

        assert isinstance(text, str)
        assert "云枢" in text
        assert "会话" in text
        assert "运行中" in text
        assert "交互次数" in text
        assert "行为模式" in text

    def test_status_reporter_get_status_text_V2增强版标记(self):
        """测试 get_status_text 在 V2 功能启用时显示增强版标记"""
        from agent.orchestrator.status_reporter import StatusReporter

        mock_orch = self._create_mock_orchestrator()
        mock_orch._v2_lifetrace = True
        reporter = StatusReporter(mock_orch)

        text = reporter.get_status_text()

        assert "V2增强版" in text


# ============================================================================
#  SubagentManager 测试
# ============================================================================

class TestSubagentManager:
    """SubagentManager 子代理创建、销毁、列表管理测试"""

    def _create_mock_orchestrator(self, subagent_enabled=True):
        """创建模拟的 Orchestrator 实例"""
        mock_orch = Mock()
        if subagent_enabled:
            mock_orch._subagent_mgr = Mock()
        else:
            mock_orch._subagent_mgr = None
        return mock_orch

    def test_subagent_manager_create_字典配置(self):
        """测试 create 方法使用字典配置创建分身"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_container = Mock()
        mock_container.id = "agent_001"
        mock_orch._subagent_mgr.create.return_value = mock_container

        mgr = SubagentManager(mock_orch)

        with patch('agent.subagent.container.SubagentConfig') as MockConfig:
            mock_config_instance = Mock()
            MockConfig.return_value = mock_config_instance

            result = mgr.create({"name": "测试分身", "model_id": "gpt-4"})

            MockConfig.assert_called_once_with(name="测试分身", model_id="gpt-4")
            mock_orch._subagent_mgr.create.assert_called_once_with(mock_config_instance)
            assert result.id == "agent_001"

    def test_subagent_manager_create_分身系统未启用时抛出异常(self):
        """测试 create 方法在分身系统未启用时抛出 RuntimeError"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator(subagent_enabled=False)
        mgr = SubagentManager(mock_orch)

        with pytest.raises(RuntimeError, match="分身系统未启用"):
            mgr.create({"name": "测试"})

    def test_subagent_manager_create_配置类型错误时抛出异常(self):
        """测试 create 方法在配置类型错误时抛出 TypeError"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mgr = SubagentManager(mock_orch)

        with pytest.raises(TypeError, match="config 必须是"):
            mgr.create("invalid_config")

    def test_subagent_manager_destroy_正常路径(self):
        """测试 destroy 方法正常销毁指定分身"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_container = Mock()
        mock_container.id = "agent_001"
        mock_orch._subagent_mgr.get.return_value = mock_container
        mock_orch._subagent_mgr.destroy.return_value = {
            "success": True,
            "memory_delta_keys": ["key1", "key2"]
        }

        mgr = SubagentManager(mock_orch)
        result = mgr.destroy("agent_001")

        mock_orch._subagent_mgr.get.assert_called_once_with("agent_001")
        mock_orch._subagent_mgr.destroy.assert_called_once_with(mock_container)
        assert result["success"] is True

    def test_subagent_manager_destroy_分身不存在时抛出异常(self):
        """测试 destroy 方法在分身不存在时抛出 ValueError"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_orch._subagent_mgr.get.return_value = None
        mgr = SubagentManager(mock_orch)

        with pytest.raises(ValueError, match="分身不存在"):
            mgr.destroy("nonexistent")

    def test_subagent_manager_destroy_系统未启用时抛出异常(self):
        """测试 destroy 方法在系统未启用时抛出 RuntimeError"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator(subagent_enabled=False)
        mgr = SubagentManager(mock_orch)

        with pytest.raises(RuntimeError, match="分身系统未启用"):
            mgr.destroy("agent_001")

    def test_subagent_manager_list_正常路径(self):
        """测试 list 方法返回所有活跃分身状态列表"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_agent1 = Mock()
        mock_agent1.get_status.return_value = {"id": "agent_001", "name": "分身1"}
        mock_agent2 = Mock()
        mock_agent2.get_status.return_value = {"id": "agent_002", "name": "分身2"}
        mock_orch._subagent_mgr.list.return_value = [mock_agent1, mock_agent2]

        mgr = SubagentManager(mock_orch)
        result = mgr.list()

        assert len(result) == 2
        assert result[0]["id"] == "agent_001"
        assert result[1]["id"] == "agent_002"

    def test_subagent_manager_list_系统未启用时返回空列表(self):
        """测试 list 方法在系统未启用时返回空列表"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator(subagent_enabled=False)
        mgr = SubagentManager(mock_orch)

        result = mgr.list()
        assert result == []

    def test_subagent_manager_get_存在时返回状态(self):
        """测试 get 方法在分身存在时返回详细状态"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_container = Mock()
        mock_container.get_status.return_value = {"id": "agent_001", "name": "测试分身", "status": "running"}
        mock_orch._subagent_mgr.get.return_value = mock_container

        mgr = SubagentManager(mock_orch)
        result = mgr.get("agent_001")

        assert result is not None
        assert result["id"] == "agent_001"
        assert result["status"] == "running"

    def test_subagent_manager_get_不存在时返回None(self):
        """测试 get 方法在分身不存在时返回 None"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_orch._subagent_mgr.get.return_value = None
        mgr = SubagentManager(mock_orch)

        result = mgr.get("nonexistent")
        assert result is None

    def test_subagent_manager_get_系统未启用时返回None(self):
        """测试 get 方法在系统未启用时返回 None"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator(subagent_enabled=False)
        mgr = SubagentManager(mock_orch)

        result = mgr.get("agent_001")
        assert result is None

    def test_subagent_manager_execute_正常路径(self):
        """测试 execute 方法在指定分身中执行任务"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_container = Mock()
        mock_result = Mock()
        mock_result.output = "任务完成"
        mock_result.trace_id = "trace_123"
        mock_result.error = None
        mock_result.duration_ms = 1500.567
        mock_result.timestamp = "2024-01-01T00:00:00Z"
        mock_container.execute.return_value = mock_result
        mock_orch._subagent_mgr.get.return_value = mock_container

        mgr = SubagentManager(mock_orch)
        result = mgr.execute("agent_001", "帮我写个Python脚本")

        mock_container.execute.assert_called_once_with("帮我写个Python脚本")
        assert result["output"] == "任务完成"
        assert result["trace_id"] == "trace_123"
        assert result["duration_ms"] == 1500.6

    def test_subagent_manager_execute_分身不存在时抛出异常(self):
        """测试 execute 方法在分身不存在时抛出 ValueError"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator()
        mock_orch._subagent_mgr.get.return_value = None
        mgr = SubagentManager(mock_orch)

        with pytest.raises(ValueError, match="分身不存在"):
            mgr.execute("nonexistent", "任务")

    def test_subagent_manager_execute_系统未启用时抛出异常(self):
        """测试 execute 方法在系统未启用时抛出 RuntimeError"""
        from agent.orchestrator.subagent_manager import SubagentManager

        mock_orch = self._create_mock_orchestrator(subagent_enabled=False)
        mgr = SubagentManager(mock_orch)

        with pytest.raises(RuntimeError, match="分身系统未启用"):
            mgr.execute("agent_001", "任务")


# ============================================================================
#  TaskDispatcher 测试
# ============================================================================

class TestTaskDispatcherSmartToolSelection:
    """TaskDispatcher 智能工具选择开关测试"""

    def test_task_dispatcher_smart_tool_selection_启用时返回True(self):
        """测试 _is_smart_tool_selection_enabled 在配置启用时返回 True"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       return_value=True):
                result = dispatcher._is_smart_tool_selection_enabled()
                assert result is True

    def test_task_dispatcher_smart_tool_selection_禁用时返回False(self):
        """测试 _is_smart_tool_selection_enabled 在配置禁用时返回 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       return_value=False):
                result = dispatcher._is_smart_tool_selection_enabled()
                assert result is False

    def test_task_dispatcher_smart_tool_selection_模块缺失时返回False(self):
        """测试 _is_smart_tool_selection_enabled 在模块缺失时返回 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       side_effect=ImportError("模块不存在")):
                result = dispatcher._is_smart_tool_selection_enabled()
                assert result is False

    def test_task_dispatcher_smart_tool_selection_异常时返回False(self):
        """测试 _is_smart_tool_selection_enabled 在异常时返回 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       side_effect=Exception("配置读取失败")):
                result = dispatcher._is_smart_tool_selection_enabled()
                assert result is False


class TestTaskDispatcherExtraSection:
    """TaskDispatcher 额外配置节开关测试"""

    def test_task_dispatcher_extra_section_存在且启用时返回True(self):
        """测试 _is_extra_section_enabled 在配置节存在且启用时返回 True"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       return_value=True):
                result = dispatcher._is_extra_section_enabled("tool_definitions")
                assert result is True

    def test_task_dispatcher_extra_section_存在但禁用时返回False(self):
        """测试 _is_extra_section_enabled 在配置节存在但禁用时返回 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       return_value=False):
                result = dispatcher._is_extra_section_enabled("tool_definitions")
                assert result is False

    def test_task_dispatcher_extra_section_不存在时使用默认值True(self):
        """测试 _is_extra_section_enabled 在配置节不存在时使用默认值 True"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       side_effect=ImportError()):
                result = dispatcher._is_extra_section_enabled("working_memory", default=True)
                assert result is True

    def test_task_dispatcher_extra_section_不存在时使用默认值False(self):
        """测试 _is_extra_section_enabled 在配置节不存在时使用默认值 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            with patch('agent.system_prompt_config.is_section_enabled',
                       side_effect=Exception()):
                result = dispatcher._is_extra_section_enabled("smart_tool_selection", default=False)
                assert result is False

    def test_task_dispatcher_extra_section_不同配置节(self):
        """测试 _is_extra_section_enabled 对不同配置节的正确处理"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()

            def mock_is_enabled(section, default=True):
                if section == "lifetrace":
                    return True
                if section == "persona":
                    return False
                if section == "working_memory":
                    return True
                return default

            with patch('agent.system_prompt_config.is_section_enabled',
                       side_effect=mock_is_enabled):
                assert dispatcher._is_extra_section_enabled("lifetrace") is True
                assert dispatcher._is_extra_section_enabled("persona") is False
                assert dispatcher._is_extra_section_enabled("working_memory") is True
                assert dispatcher._is_extra_section_enabled("unknown_section") is True


class TestTaskDispatcherNeedsPlanning:
    """TaskDispatcher 规划需求判断测试"""

    def test_task_dispatcher_needs_planning_规划引擎未启用时返回False(self):
        """测试 _needs_planning 在规划引擎未启用时返回 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()
            dispatcher._planner = None
            dispatcher._planning_enabled = False

            result = dispatcher._needs_planning("帮我完成一个复杂任务")
            assert result is False

    def test_task_dispatcher_needs_planning_简单任务返回False(self):
        """测试 _needs_planning 对简单任务返回 False"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()
            dispatcher._planner = Mock()
            dispatcher._planning_enabled = True

            result = dispatcher._needs_planning("你好")
            assert result is False

    def test_task_dispatcher_needs_planning_复杂关键词触发(self):
        """测试 _needs_planning 在包含复杂关键词时返回 True"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()
            dispatcher._planner = Mock()
            dispatcher._planning_enabled = True

            result = dispatcher._needs_planning("帮我完成这个项目的构建流程")
            assert result is True

    def test_task_dispatcher_needs_planning_多个动作关键词触发(self):
        """测试 _needs_planning 在包含多个动作关键词时返回 True"""
        from agent.orchestrator.task_dispatcher import TaskDispatcher

        with patch.object(TaskDispatcher, '__init__', lambda self: None):
            dispatcher = TaskDispatcher()
            dispatcher._planner = Mock()
            dispatcher._planning_enabled = True

            result = dispatcher._needs_planning("检查并分析系统性能，创建监控报告")
            assert result is True
