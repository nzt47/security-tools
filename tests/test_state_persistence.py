"""状态持久化功能测试用例

测试覆盖：
- 状态保存功能
- 状态恢复功能
- 日志级别动态调整
- 状态文件管理
"""

import os
import sys
import json
import tempfile
import shutil
import logging
import pytest
from datetime import datetime, timezone

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.state_manager import (
    StateManager,
    StateSaveResult,
    StateLoadResult,
    StateInfo,
    get_state_manager,
    save_state,
    load_state,
    set_log_level,
    get_log_level,
)


class TestStateManager:
    """状态管理器单元测试"""
    
    def setup_method(self):
        """每个测试方法前的设置"""
        # 创建临时目录用于测试
        self.temp_dir = tempfile.mkdtemp()
        # 创建独立的状态管理器实例，避免干扰全局实例
        self.state_manager = StateManager(state_dir=self.temp_dir, auto_save_interval=0)
    
    def teardown_method(self):
        """每个测试方法后的清理"""
        # 删除临时目录
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_save_state_basic(self):
        """测试基本状态保存功能"""
        test_data = {
            "key1": "value1",
            "number": 42,
            "nested": {"a": 1, "b": 2},
            "list": [1, 2, 3],
        }
        
        result = self.state_manager.save_state(test_data)
        
        assert result.success is True
        assert result.state_id is not None
        assert len(result.state_id) > 0
        assert result.file_path.endswith(".json")
        assert result.data_size > 0
        assert result.created_at is not None
        
        # 验证文件存在
        assert os.path.exists(result.file_path)
    
    def test_load_state_basic(self):
        """测试基本状态加载功能"""
        test_data = {
            "test_key": "test_value",
            "count": 100,
        }
        
        # 先保存
        save_result = self.state_manager.save_state(test_data)
        assert save_result.success is True
        
        # 再加载
        load_result = self.state_manager.load_state()
        
        assert load_result.success is True
        assert load_result.state_id == save_result.state_id
        assert load_result.state_data.get("test_key") == "test_value"
        assert load_result.state_data.get("count") == 100
    
    def test_save_load_with_custom_state_id(self):
        """测试使用自定义状态ID保存和加载"""
        custom_id = "test_custom_state_2024"
        test_data = {"custom": "data"}
        
        # 使用自定义ID保存
        save_result = self.state_manager.save_state(test_data, state_id=custom_id)
        assert save_result.success is True
        assert save_result.state_id == custom_id
        
        # 使用自定义ID加载
        load_result = self.state_manager.load_state(state_id=custom_id)
        assert load_result.success is True
        assert load_result.state_data.get("custom") == "data"
    
    def test_state_persistence_roundtrip(self):
        """测试状态保存和加载的往返一致性"""
        original_data = {
            "string": "hello world",
            "integer": 42,
            "float": 3.14159,
            "boolean": True,
            "none": None,
            "list": [1, "two", 3.0],
            "dict": {"nested": {"key": "value"}},
            "datetime_str": datetime.now(timezone.utc).isoformat(),
        }
        
        # 保存
        save_result = self.state_manager.save_state(original_data)
        assert save_result.success is True
        
        # 创建新的管理器实例来加载
        new_manager = StateManager(state_dir=self.temp_dir, auto_save_interval=0)
        load_result = new_manager.load_state(state_id=save_result.state_id)
        
        assert load_result.success is True
        loaded_data = load_result.state_data
        
        # 验证数据完整性（排除元数据）
        assert loaded_data.get("string") == "hello world"
        assert loaded_data.get("integer") == 42
        assert abs(loaded_data.get("float") - 3.14159) < 0.0001
        assert loaded_data.get("boolean") is True
        assert loaded_data.get("none") is None
        assert loaded_data.get("list") == [1, "two", 3.0]
        assert loaded_data.get("dict") == {"nested": {"key": "value"}}
    
    def test_metadata_in_state(self):
        """测试状态元数据是否正确添加"""
        test_data = {"data": "test"}
        
        result = self.state_manager.save_state(test_data)
        assert result.success is True
        
        load_result = self.state_manager.load_state()
        metadata = load_result.state_data.get("_metadata")
        
        assert metadata is not None
        assert metadata.get("state_id") == result.state_id
        assert metadata.get("version") == "1.0"
        assert "created_at" in metadata
        assert "data_size" in metadata
    
    def test_list_states(self):
        """测试列出状态文件功能"""
        # 保存多个状态（使用自定义state_id）
        for i in range(3):
            self.state_manager.save_state({"index": i}, state_id=f"state_{i}")
        
        states = self.state_manager.list_states()
        
        assert len(states) == 3
        for state in states:
            assert isinstance(state, StateInfo)
            assert state.state_id is not None
            assert state.file_path is not None
            assert state.created_at is not None
            assert state.data_size > 0
    
    def test_delete_state(self):
        """测试删除状态文件功能"""
        # 使用自定义state_id保存一个状态
        custom_id = "test_delete_state"
        result = self.state_manager.save_state({"test": "data"}, state_id=custom_id)
        assert result.success is True
        
        # 验证存在
        states_before = self.state_manager.list_states()
        assert len(states_before) == 1
        
        # 删除
        delete_result = self.state_manager.delete_state(custom_id)
        assert delete_result is True
        
        # 验证已删除
        states_after = self.state_manager.list_states()
        assert len(states_after) == 0
    
    def test_load_nonexistent_state(self):
        """测试加载不存在的状态"""
        result = self.state_manager.load_state(state_id="nonexistent_id")
        
        assert result.success is False
        assert "不存在" in result.error_message
    
    def test_empty_state_dir(self):
        """测试空状态目录"""
        result = self.state_manager.load_state()
        
        assert result.success is False
        assert "不存在" in result.error_message
    
    def test_update_state(self):
        """测试增量更新状态"""
        self.state_manager.update_state({"key1": "value1"})
        self.state_manager.update_state({"key2": "value2"})
        
        current = self.state_manager.get_current_state()
        
        assert current.get("key1") == "value1"
        assert current.get("key2") == "value2"
    
    def test_clear_state(self):
        """测试清除状态"""
        self.state_manager.update_state({"key": "value"})
        self.state_manager.clear_state()
        
        current = self.state_manager.get_current_state()
        assert len(current) == 0


class TestLogLevelManagement:
    """日志级别管理测试"""
    
    def test_set_log_level_valid(self):
        """测试设置有效的日志级别"""
        # 设置DEBUG级别
        result = set_log_level("DEBUG")
        assert result is True
        
        # 验证级别已更改
        level = get_log_level()
        assert level == "DEBUG"
    
    def test_set_log_level_invalid(self):
        """测试设置无效的日志级别"""
        result = set_log_level("INVALID")
        assert result is False
    
    def test_get_log_level(self):
        """测试获取日志级别"""
        # 默认应该是INFO
        level = get_log_level()
        assert level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    
    def test_set_log_level_for_specific_logger(self):
        """测试为特定日志记录器设置级别"""
        # 创建一个测试日志记录器
        test_logger = logging.getLogger("test_state_logger")
        test_logger.setLevel(logging.INFO)
        
        # 设置级别
        result = set_log_level("DEBUG", "test_state_logger")
        assert result is True
        
        # 验证
        level = get_log_level("test_state_logger")
        assert level == "DEBUG"
    
    def test_list_loggers(self):
        """测试列出日志记录器"""
        manager = get_state_manager()
        loggers = manager.list_loggers()
        
        assert len(loggers) > 0
        # 应该包含根日志记录器
        logger_names = [name for name, _ in loggers]
        assert "root" in logger_names


class TestConvenienceFunctions:
    """便捷函数测试"""
    
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_save_state_convenience(self):
        """测试save_state便捷函数"""
        # 注意：这里使用全局单例，测试可能会互相影响
        result = save_state({"test": "convenience"})
        assert result.success is True
    
    def test_load_state_convenience(self):
        """测试load_state便捷函数"""
        save_state({"test": "convenience"})
        result = load_state()
        assert result.success is True


class TestStateManagerEdgeCases:
    """边界情况测试"""
    
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_manager = StateManager(state_dir=self.temp_dir, auto_save_interval=0)
    
    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_large_state_data(self):
        """测试保存较大的状态数据"""
        large_data = {
            "large_list": list(range(1000)),
            "large_string": "x" * 10000,
            "nested_dict": {f"key_{i}": f"value_{i}" for i in range(100)},
        }
        
        result = self.state_manager.save_state(large_data)
        assert result.success is True
        assert result.data_size > 0
    
    def test_state_with_special_characters(self):
        """测试包含特殊字符的状态数据"""
        special_data = {
            "chinese": "中文测试",
            "emoji": "🎉",
            "newline": "line1\nline2\nline3",
            "unicode": "नमस्ते",
        }
        
        result = self.state_manager.save_state(special_data)
        assert result.success is True
        
        load_result = self.state_manager.load_state()
        assert load_result.success is True
        assert load_result.state_data.get("chinese") == "中文测试"
        assert load_result.state_data.get("emoji") == "🎉"
    
    def test_corrupted_state_file(self):
        """测试加载损坏的状态文件"""
        # 创建一个损坏的JSON文件
        corrupted_path = os.path.join(self.temp_dir, "corrupted.json")
        with open(corrupted_path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        
        result = self.state_manager.load_state(state_id="corrupted")
        assert result.success is False
        assert "JSON解析错误" in result.error_message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])