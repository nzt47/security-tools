"""Extension Store 单元测试"""
import pytest
import json
from pathlib import Path

from agent.extensions.base import ExtensionMetadata, ExtensionType, ExtensionStatus
from agent.extensions.store import ExtensionStore


class TestExtensionStore:
    """测试扩展存储"""

    def test_extension_store_init(self, tmp_path):
        """测试初始化"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        assert store._data_file == data_file

    def test_extension_store_add_metadata(self, tmp_path):
        """测试添加元数据"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata = ExtensionMetadata(
            ext_id="test_ext",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0",
            description="Test"
        )
        
        store.add(metadata)
        
        assert data_file.exists()
        
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert len(data["skills"]) == 1
            assert data["skills"][0]["ext_id"] == "test_ext"
            assert data["skills"][0]["name"] == "Test"

    def test_extension_store_get_metadata(self, tmp_path):
        """测试获取元数据"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata = ExtensionMetadata(
            ext_id="test_ext",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0",
            description="Test"
        )
        store.add(metadata)
        
        loaded = store.get(ExtensionType.SKILL, "test_ext")
        
        assert loaded is not None
        assert loaded["ext_id"] == "test_ext"
        assert loaded["name"] == "Test"

    def test_extension_store_get_metadata_not_found(self, tmp_path):
        """测试获取不存在的元数据"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        loaded = store.get(ExtensionType.SKILL, "nonexistent")
        
        assert loaded is None

    def test_extension_store_list_all(self, tmp_path):
        """测试列出所有扩展"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata1 = ExtensionMetadata(
            ext_id="skill1",
            ext_type=ExtensionType.SKILL,
            name="Skill1",
            version="1.0.0"
        )
        metadata2 = ExtensionMetadata(
            ext_id="mcp1",
            ext_type=ExtensionType.MCP,
            name="MCP1",
            version="2.0.0"
        )
        
        store.add(metadata1)
        store.add(metadata2)
        
        extensions = store.list_all()
        
        assert len(extensions) == 2

    def test_extension_store_list_by_type(self, tmp_path):
        """测试按类型列出扩展"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata1 = ExtensionMetadata(
            ext_id="skill1",
            ext_type=ExtensionType.SKILL,
            name="Skill1",
            version="1.0.0"
        )
        metadata2 = ExtensionMetadata(
            ext_id="mcp1",
            ext_type=ExtensionType.MCP,
            name="MCP1",
            version="2.0.0"
        )
        
        store.add(metadata1)
        store.add(metadata2)
        
        skills = store.list_all(ExtensionType.SKILL)
        
        assert len(skills) == 1
        assert skills[0]["ext_id"] == "skill1"

    def test_extension_store_remove_metadata(self, tmp_path):
        """测试删除元数据"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata = ExtensionMetadata(
            ext_id="test_ext",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0"
        )
        store.add(metadata)
        
        assert store.get(ExtensionType.SKILL, "test_ext") is not None
        
        result = store.remove(ExtensionType.SKILL, "test_ext")
        
        assert result is True
        assert store.get(ExtensionType.SKILL, "test_ext") is None

    def test_extension_store_remove_not_found(self, tmp_path):
        """测试删除不存在的元数据"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        result = store.remove(ExtensionType.SKILL, "nonexistent")
        
        assert result is False

    def test_extension_store_update_status(self, tmp_path):
        """测试更新状态"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata = ExtensionMetadata(
            ext_id="test_ext",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0"
        )
        store.add(metadata)
        
        result = store.update_status(ExtensionType.SKILL, "test_ext", ExtensionStatus.ENABLED)
        
        assert result is True
        
        loaded = store.get(ExtensionType.SKILL, "test_ext")
        assert loaded["status"] == "enabled"

    def test_extension_store_update_config(self, tmp_path):
        """测试更新配置"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata = ExtensionMetadata(
            ext_id="test_ext",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0"
        )
        store.add(metadata)
        
        result = store.update_config(ExtensionType.SKILL, "test_ext", {"timeout": 60})
        
        assert result is True
        
        loaded = store.get(ExtensionType.SKILL, "test_ext")
        assert loaded["config"]["timeout"] == 60

    def test_extension_store_clear_cache(self, tmp_path):
        """测试清空缓存"""
        data_file = tmp_path / "extensions.json"
        store = ExtensionStore(str(data_file))
        
        metadata = ExtensionMetadata(
            ext_id="test_ext",
            ext_type=ExtensionType.SKILL,
            name="Test",
            version="1.0.0"
        )
        store.add(metadata)
        
        assert store._cache is not None
        
        store.clear_cache()
        
        assert store._cache is None