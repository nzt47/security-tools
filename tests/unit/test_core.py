"""
Core 模块测试 - pytest 格式
验证统一抽象层是否正常工作
"""
import pytest
import tempfile
from pathlib import Path
import logging

from core.storage import (
    JSONFileStorage,
    InMemoryStorage,
    create_storage
)
from core.registry import SimpleRegistry
from core.config import Config
from core.logging import log_section

logger = logging.getLogger(__name__)


class TestInMemoryStorage:
    """测试 InMemoryStorage 类"""
    
    @pytest.fixture
    def storage(self):
        """创建一个空的 InMemoryStorage 实例"""
        return InMemoryStorage()
    
    def test_save_and_load_basic(self, storage):
        """测试基本的保存和加载功能"""
        storage.save("test_key", {"value": 123})
        
        assert storage.exists("test_key")
        loaded = storage.load("test_key")
        assert loaded == {"value": 123}
    
    def test_load_with_default(self, storage):
        """测试加载不存在的键时返回默认值"""
        result = storage.load("non_existent", {"default": 42})
        assert result == {"default": 42}
    
    def test_delete(self, storage):
        """测试删除功能"""
        storage.save("key1", "value1")
        assert storage.exists("key1")
        
        storage.delete("key1")
        assert not storage.exists("key1")
    
    def test_overwrite_existing(self, storage):
        """测试覆盖已有键"""
        storage.save("key", {"v": 1})
        storage.save("key", {"v": 2})
        
        loaded = storage.load("key")
        assert loaded == {"v": 2}


class TestJSONFileStorage:
    """测试 JSONFileStorage 类"""
    
    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)
    
    @pytest.fixture
    def storage(self, temp_dir):
        """创建 JSONFileStorage 实例"""
        return JSONFileStorage(str(temp_dir))
    
    def test_save_and_load_file(self, storage):
        """测试文件存储的保存和加载"""
        storage.save("test_key", {"value": 123})
        
        loaded = storage.load("test_key")
        assert loaded == {"value": 123}
    
    def test_multiple_files(self, storage):
        """测试多个文件存储"""
        storage.save("key1", "value1")
        storage.save("key2", "value2")
        
        assert storage.exists("key1")
        assert storage.exists("key2")
    
    def test_delete_file(self, storage):
        """测试删除文件"""
        storage.save("key", "data")
        assert storage.exists("key")
        
        storage.delete("key")
        assert not storage.exists("key")


class TestStorageFactory:
    """测试存储工厂函数"""
    
    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as td:
            yield td
    
    def test_create_memory_storage(self):
        """测试创建内存存储"""
        storage = create_storage("memory")
        assert isinstance(storage, InMemoryStorage)
    
    def test_create_json_storage(self, temp_dir):
        """测试创建 JSON 存储"""
        storage = create_storage("json", base_dir=temp_dir)
        assert isinstance(storage, JSONFileStorage)


class TestSimpleRegistry:
    """测试简单注册器"""
    
    @pytest.fixture
    def registry(self):
        """创建空的注册器"""
        return SimpleRegistry()
    
    def test_register_and_get(self, registry):
        """测试注册和获取"""
        def test_func():
            return "test"
        
        registry.register("test", test_func)
        assert registry.get("test") is test_func


@pytest.mark.p0
@pytest.mark.unit
def test_all_core_functionality():
    """综合测试 - P0 优先级 - 简化版本"""
    # 存储测试
    storage = InMemoryStorage()
    storage.save("test", "data")
    assert storage.load("test") == "data"
    
    # 注册器测试
    registry = SimpleRegistry()
    registry.register("key", lambda: 42)
    assert registry.get("key")() == 42
    
    logger.info("✅ 所有 Core 模块测试通过!")
