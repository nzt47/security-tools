"""
Core 模块全面测试 - pytest 格式
验证统一抽象层所有功能是否正常工作
"""
import pytest
import tempfile
import os
import json
from pathlib import Path
import logging
from unittest.mock import patch, MagicMock

from core.storage import (
    JSONFileStorage,
    InMemoryStorage,
    StorableItem,
    create_storage,
    BaseStorage
)
from core.registry import (
    SimpleRegistry,
    CallbackRegistry,
    TypeRegistry,
    register,
    BaseRegistry
)
from core.config import Config
from core.logging import (
    log_section,
    log_operation,
    setup_logger,
    ProgressLogger
)

logger = logging.getLogger(__name__)


class TestConfig:
    """测试 Config 类"""
    
    def test_get_simple_key(self):
        """测试获取简单键"""
        config = Config({"a": 1, "b": "test"})
        assert config.get("a") == 1
        assert config.get("b") == "test"
    
    def test_get_nested_key(self):
        """测试获取嵌套键"""
        config = Config({"a": {"b": {"c": 42}}})
        assert config.get("a.b.c") == 42
    
    def test_get_with_default(self):
        """测试获取不存在的键时返回默认值"""
        config = Config({"a": 1})
        assert config.get("nonexistent", "default") == "default"
    
    def test_set_simple_key(self):
        """测试设置简单键"""
        config = Config()
        config.set("key", "value")
        assert config.get("key") == "value"
    
    def test_set_nested_key(self):
        """测试设置嵌套键"""
        config = Config()
        config.set("a.b.c", 123)
        assert config.get("a.b.c") == 123
    
    def test_merge_config(self):
        """测试合并配置"""
        config = Config({"a": 1, "b": {"c": 2}})
        config.merge({"b": {"d": 3}, "e": 4})
        assert config.get("a") == 1
        assert config.get("b.c") == 2
        assert config.get("b.d") == 3
        assert config.get("e") == 4
    
    def test_to_dict(self):
        """测试转换为字典"""
        data = {"a": 1, "b": 2}
        config = Config(data)
        assert config.to_dict() == data
    
    def test_save_and_load_file(self):
        """测试保存和加载配置文件"""
        with tempfile.TemporaryDirectory() as td:
            filepath = Path(td) / "config.json"
            config = Config({"a": 1, "b": "test"})
            config.save(str(filepath))
            
            loaded = Config.load(str(filepath))
            assert loaded.get("a") == 1
            assert loaded.get("b") == "test"
    
    def test_load_nonexistent_file(self):
        """测试加载不存在的文件"""
        config = Config.load("/nonexistent/path/config.json")
        assert config.to_dict() == {}
    
    def test_from_env(self):
        """测试从环境变量加载"""
        with patch.dict(os.environ, {"YUNSHU_TEST_KEY": "value", "YUNSHU_NESTED__PATH": "nested_value"}):
            config = Config.from_env("YUNSHU_")
            assert config.get("test_key") == "value"
            assert config.get("nested.path") == "nested_value"
    
    def test_repr(self):
        """测试repr方法"""
        config = Config({"a": 1})
        assert "Config" in repr(config)


class TestLogging:
    """测试日志工具"""
    
    def test_log_section(self):
        """测试log_section函数"""
        mock_logger = MagicMock()
        log_section(mock_logger, "Test Section", {"key1": "value1", "key2": "value2"})
        
        calls = mock_logger.log.call_args_list
        assert len(calls) == 3  # title + 2 items
    
    def test_log_section_empty(self):
        """测试空items的log_section"""
        mock_logger = MagicMock()
        log_section(mock_logger, "Empty Section", {})
        
        calls = mock_logger.log.call_args_list
        assert len(calls) == 1  # only title
    
    def test_log_operation_done(self):
        """测试log_operation成功状态"""
        mock_logger = MagicMock()
        log_operation(mock_logger, "test_operation", "done")
        
        mock_logger.info.assert_called_once()
    
    def test_log_operation_error(self):
        """测试log_operation错误状态"""
        mock_logger = MagicMock()
        log_operation(mock_logger, "test_operation", "error", {"detail": "failed"})
        
        mock_logger.info.assert_called_once()
    
    def test_log_operation_pending(self):
        """测试log_operation进行中状态"""
        mock_logger = MagicMock()
        log_operation(mock_logger, "test_operation", "pending")
        
        mock_logger.info.assert_called_once()
    
    def test_setup_logger(self):
        """测试setup_logger函数"""
        logger = setup_logger("test_logger", logging.DEBUG)
        assert logger.name == "test_logger"
        assert logger.level == logging.DEBUG
    
    def test_setup_logger_with_file(self, tmp_path):
        """测试带文件输出的setup_logger"""
        log_file = tmp_path / "test.log"
        logger = setup_logger("test_file_logger", log_file=str(log_file))
        
        logger.info("test message")
        assert log_file.exists()
    
    def test_progress_logger(self):
        """测试ProgressLogger"""
        mock_logger = MagicMock()
        progress = ProgressLogger(mock_logger, 10, "Test")
        
        progress.update(1, "Step 1")
        progress.update(2)
        progress.finish("Complete")
        
        assert mock_logger.info.call_count == 3


class TestRegistry:
    """测试注册表类"""
    
    def test_base_registry_is_abstract(self):
        """测试BaseRegistry是抽象类"""
        with pytest.raises(TypeError):
            BaseRegistry()
    
    def test_simple_registry_has(self):
        """测试SimpleRegistry的has方法"""
        registry = SimpleRegistry()
        registry.register("key", "value")
        assert registry.has("key")
        assert not registry.has("nonexistent")
    
    def test_simple_registry_list(self):
        """测试SimpleRegistry的list方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)
        registry.register("b", 2)
        
        keys = registry.list()
        assert "a" in keys
        assert "b" in keys
    
    def test_simple_registry_remove(self):
        """测试SimpleRegistry的remove方法"""
        registry = SimpleRegistry()
        registry.register("key", "value")
        
        assert registry.remove("key")
        assert not registry.has("key")
        assert not registry.remove("nonexistent")
    
    def test_simple_registry_clear(self):
        """测试SimpleRegistry的clear方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)
        registry.register("b", 2)
        
        registry.clear()
        assert registry.count() == 0
    
    def test_simple_registry_all(self):
        """测试SimpleRegistry的all方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)
        
        all_items = registry.all()
        assert all_items == {"a": 1}
    
    def test_simple_registry_count(self):
        """测试SimpleRegistry的count方法"""
        registry = SimpleRegistry()
        assert registry.count() == 0
        
        registry.register("a", 1)
        assert registry.count() == 1
    
    def test_simple_registry_update(self):
        """测试SimpleRegistry的update方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)
        
        registry.update({"b": 2, "c": 3})
        assert registry.count() == 3
    
    def test_callback_registry_trigger(self):
        """测试CallbackRegistry的trigger方法"""
        registry = CallbackRegistry()
        
        def test_func(x, y):
            return x + y
        
        registry.register("add", test_func)
        result = registry.trigger("add", 2, 3)
        assert result == 5
    
    def test_callback_registry_trigger_nonexistent(self):
        """测试触发不存在的回调"""
        registry = CallbackRegistry()
        result = registry.trigger("nonexistent")
        assert result is None
    
    def test_type_registry_create_instance(self):
        """测试TypeRegistry的create_instance方法"""
        registry = TypeRegistry()
        
        class TestClass:
            def __init__(self, value):
                self.value = value
        
        registry.register("TestClass", TestClass)
        instance = registry.create_instance("TestClass", value=42)
        assert isinstance(instance, TestClass)
        assert instance.value == 42
    
    def test_type_registry_create_nonexistent(self):
        """测试创建不存在的类型"""
        registry = TypeRegistry()
        instance = registry.create_instance("Nonexistent")
        assert instance is None
    
    def test_register_decorator(self):
        """测试register装饰器"""
        registry = SimpleRegistry()
        
        @register(registry, "my_func")
        def my_func():
            return "test"
        
        assert registry.get("my_func") == my_func


class TestStorage:
    """测试存储类"""
    
    def test_base_storage_is_abstract(self):
        """测试BaseStorage是抽象类"""
        with pytest.raises(TypeError):
            BaseStorage()
    
    def test_storable_item(self):
        """测试StorableItem数据类"""
        item = StorableItem(id="1", created_at="2024-01-01", updated_at="2024-01-01")
        
        data = item.to_dict()
        assert data["id"] == "1"
        
        restored = StorableItem.from_dict(data)
        assert restored.id == "1"
    
    def test_json_storage_list_keys(self, tmp_path):
        """测试JSONFileStorage的list_keys方法"""
        storage = JSONFileStorage(str(tmp_path))
        storage.save("key1", "value1")
        storage.save("key2", "value2")
        storage.save("prefix_key3", "value3")
        
        keys = storage.list_keys()
        assert "key1" in keys
        assert "key2" in keys
        assert "prefix_key3" in keys
        
        prefix_keys = storage.list_keys("prefix")
        assert len(prefix_keys) == 1
        assert "prefix_key3" in prefix_keys
    
    def test_json_storage_load_default(self, tmp_path):
        """测试JSONFileStorage加载不存在的键"""
        storage = JSONFileStorage(str(tmp_path))
        result = storage.load("nonexistent", "default")
        assert result == "default"
    
    def test_json_storage_save_with_special_characters(self, tmp_path):
        """测试JSONFileStorage保存带特殊字符的键"""
        storage = JSONFileStorage(str(tmp_path))
        storage.save("path/to/file", {"data": "test"})
        
        assert storage.exists("path_to_file")  # 路径被规范化
    
    def test_json_storage_error_handling(self, tmp_path):
        """测试JSONFileStorage的错误处理"""
        storage = JSONFileStorage(str(tmp_path))
        
        # 创建无效的JSON文件
        invalid_file = tmp_path / "invalid.json"
        invalid_file.write_text("invalid json")
        
        result = storage.load("invalid")
        assert result is None
    
    def test_create_storage_invalid_type(self):
        """测试create_storage传入无效类型"""
        with pytest.raises(ValueError):
            create_storage("invalid_type")
    
    def test_in_memory_storage_list_keys_with_prefix(self):
        """测试InMemoryStorage的list_keys带前缀过滤"""
        storage = InMemoryStorage()
        storage.save("user_1", "data1")
        storage.save("user_2", "data2")
        storage.save("admin_1", "data3")
        
        keys = storage.list_keys("user")
        assert len(keys) == 2
        assert "user_1" in keys
        assert "user_2" in keys
    
    def test_json_storage_save_exception(self, tmp_path):
        """测试JSONFileStorage.save的异常处理"""
        storage = JSONFileStorage(str(tmp_path))
        
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                storage.save("test", {"data": "value"})
    
    def test_json_storage_delete_nonexistent(self, tmp_path):
        """测试JSONFileStorage.delete删除不存在的文件"""
        storage = JSONFileStorage(str(tmp_path))
        result = storage.delete("nonexistent")
        assert result is False
    
    def test_json_storage_delete_exception(self, tmp_path):
        """测试JSONFileStorage.delete的异常处理"""
        storage = JSONFileStorage(str(tmp_path))
        storage.save("test", {"data": "value"})
        
        with patch("pathlib.Path.unlink", side_effect=PermissionError("Permission denied")):
            result = storage.delete("test")
            assert result is False
    
    def test_json_storage_get_filepath_normal_key(self, tmp_path):
        """测试_get_filepath方法对正常键的处理"""
        storage = JSONFileStorage(str(tmp_path))
        
        filepath = storage._get_filepath("test_key")
        expected = tmp_path / "test_key.json"
        assert filepath == expected
    
    def test_json_storage_get_filepath_path_traversal(self, tmp_path):
        """测试_get_filepath方法的路径遍历防护"""
        storage = JSONFileStorage(str(tmp_path))
        
        filepath = storage._get_filepath("../../etc/passwd")
        assert ".." not in str(filepath)
        assert filepath.parent == tmp_path
        assert "passwd" in str(filepath.stem)
    
    def test_json_storage_get_filepath_slashes(self, tmp_path):
        """测试_get_filepath方法对斜杠的处理"""
        storage = JSONFileStorage(str(tmp_path))
        
        filepath = storage._get_filepath("path/to/file")
        assert "path_to_file" in str(filepath.stem)
        
        filepath = storage._get_filepath("path\\to\\file")
        assert "path_to_file" in str(filepath.stem)
    
    def test_in_memory_storage_delete_nonexistent(self):
        """测试InMemoryStorage.delete删除不存在的键"""
        storage = InMemoryStorage()
        result = storage.delete("nonexistent")
        assert result is False
    
    def test_in_memory_storage_exists(self):
        """测试InMemoryStorage.exists方法"""
        storage = InMemoryStorage()
        
        assert storage.exists("nonexistent") is False
        
        storage.save("test_key", "test_value")
        assert storage.exists("test_key") is True


class TestCreateStorage:
    """测试create_storage函数"""
    
    def test_create_storage_json_with_invalid_args(self, tmp_path):
        """测试create_storage传入JSON存储的无效参数"""
        storage = create_storage("json", base_dir=str(tmp_path), invalid_arg="test")
        
        assert isinstance(storage, JSONFileStorage)
        assert storage.base_dir == tmp_path
    
    def test_create_storage_memory_with_args(self):
        """测试create_storage传入内存存储的参数被忽略"""
        storage = create_storage("memory", base_dir="/some/path", extra_arg="test")
        
        assert isinstance(storage, InMemoryStorage)


@pytest.mark.p0
@pytest.mark.unit
def test_core_module_import():
    """测试core模块导入"""
    from core import (
        BaseStorage,
        JSONFileStorage,
        InMemoryStorage,
        StorableItem,
        create_storage,
        BaseRegistry,
        SimpleRegistry,
        CallbackRegistry,
        TypeRegistry,
        register,
        Config,
        log_section,
        log_operation,
        setup_logger,
        ProgressLogger,
        __version__
    )
    
    assert __version__ == '0.1.0'