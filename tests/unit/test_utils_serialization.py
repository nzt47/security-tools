"""Serialization 单元测试"""
import pytest
import json
from pathlib import Path

from agent.utils.serialization import (
    Serializer,
    get_serializer,
    HAS_MSGPACK,
    HAS_CBOR,
)


class TestSerializer:
    """测试序列化器"""

    def test_serializer_init_json(self):
        """测试 JSON 序列化器初始化"""
        serializer = Serializer(format="json")
        
        assert serializer.format == "json"
        assert serializer.compress is False

    def test_serializer_dumps_json(self):
        """测试 JSON 序列化"""
        serializer = Serializer(format="json")
        data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        
        result = serializer.dumps(data)
        
        assert isinstance(result, bytes)
        loaded = json.loads(result.decode())
        assert loaded["key"] == "value"
        assert loaded["number"] == 42

    def test_serializer_loads_json(self):
        """测试 JSON 反序列化"""
        serializer = Serializer(format="json")
        data = b'{"key": "value", "number": 42}'
        
        result = serializer.loads(data)
        
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_serializer_dumps_pickle(self):
        """测试 Pickle 序列化"""
        serializer = Serializer(format="pickle")
        data = {"key": "value", "number": 42}
        
        result = serializer.dumps(data)
        
        assert isinstance(result, bytes)

    def test_serializer_loads_pickle(self):
        """测试 Pickle 反序列化"""
        serializer = Serializer(format="pickle")
        data = {"key": "value", "number": 42}
        
        dumped = serializer.dumps(data)
        result = serializer.loads(dumped)
        
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_serializer_dump_load_file(self, tmp_path):
        """测试文件序列化和反序列化"""
        serializer = Serializer(format="json")
        data = {"key": "value", "number": 42}
        
        file_path = tmp_path / "test.json"
        serializer.dump(data, file_path)
        
        assert file_path.exists()
        
        loaded = serializer.load(file_path)
        
        assert loaded["key"] == "value"
        assert loaded["number"] == 42

    def test_serializer_load_nonexistent_file(self, tmp_path):
        """测试加载不存在的文件"""
        serializer = Serializer(format="json")
        file_path = tmp_path / "nonexistent.json"
        
        result = serializer.load(file_path)
        
        assert result is None

    def test_get_serializer_json(self):
        """测试获取 JSON 序列化器"""
        serializer = get_serializer("json")
        
        assert isinstance(serializer, Serializer)
        assert serializer.format == "json"

    def test_get_serializer_pickle(self):
        """测试获取 Pickle 序列化器"""
        serializer = get_serializer("pickle")
        
        assert isinstance(serializer, Serializer)
        assert serializer.format == "pickle"

    def test_get_serializer_unknown_format(self):
        """测试未知格式回退到 JSON"""
        serializer = get_serializer("unknown")
        
        assert isinstance(serializer, Serializer)
        assert serializer.format == "json"

    def test_msgpack_fallback(self):
        """测试 msgpack 不可用时回退"""
        serializer = Serializer(format="msgpack")
        
        if not HAS_MSGPACK:
            assert serializer.format == "json"
        else:
            assert serializer.format == "msgpack"

    def test_cbor_fallback(self):
        """测试 cbor 不可用时回退"""
        serializer = Serializer(format="cbor")
        
        if not HAS_CBOR:
            assert serializer.format == "json"
        else:
            assert serializer.format == "cbor"