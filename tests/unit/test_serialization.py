"""
序列化模块测试
"""

import pytest
import tempfile
import os
from pathlib import Path

from agent.utils.serialization import (
    Serializer,
    get_serializer,
    benchmark_serialization,
    HAS_MSGPACK,
    HAS_CBOR,
)


class TestSerializer:
    """测试序列化器类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_json(self):
        """测试JSON序列化器"""
        serializer = Serializer(format="json")
        test_data = {"key": "value", "number": 123, "list": [1, 2, 3]}
        
        serialized = serializer.dumps(test_data)
        assert isinstance(serialized, bytes)
        
        deserialized = serializer.loads(serialized)
        assert deserialized == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_pickle(self):
        """测试Pickle序列化器"""
        serializer = Serializer(format="pickle")
        test_data = {"key": "value", "number": 123, "list": [1, 2, 3]}
        
        serialized = serializer.dumps(test_data)
        assert isinstance(serialized, bytes)
        
        deserialized = serializer.loads(serialized)
        assert deserialized == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_msgpack(self):
        """测试MsgPack序列化器"""
        serializer = Serializer(format="msgpack")
        test_data = {"key": "value", "number": 123, "list": [1, 2, 3]}
        
        serialized = serializer.dumps(test_data)
        assert isinstance(serialized, bytes)
        
        deserialized = serializer.loads(serialized)
        assert deserialized == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_cbor(self):
        """测试CBOR序列化器"""
        serializer = Serializer(format="cbor")
        test_data = {"key": "value", "number": 123, "list": [1, 2, 3]}
        
        serialized = serializer.dumps(test_data)
        assert isinstance(serialized, bytes)
        
        deserialized = serializer.loads(serialized)
        assert deserialized == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_unknown_format(self):
        """测试未知格式降级为JSON"""
        serializer = Serializer(format="unknown_format")
        assert serializer.format == "json"
        
        test_data = {"key": "value"}
        serialized = serializer.dumps(test_data)
        deserialized = serializer.loads(serialized)
        assert deserialized == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_dump_load_file(self):
        """测试文件读写"""
        serializer = Serializer(format="json")
        test_data = {"key": "value", "nested": {"a": 1, "b": [1, 2, 3]}}
        
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            serializer.dump(test_data, filepath)
            
            assert filepath.exists()
            
            loaded_data = serializer.load(filepath)
            assert loaded_data == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_dump_load_compressed(self):
        """测试压缩文件读写"""
        serializer = Serializer(format="json", compress=True)
        test_data = {"key": "value", "data": "x" * 1000}
        
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            serializer.dump(test_data, filepath)
            
            # 压缩文件应该有 .gz 后缀
            compressed_path = Path(str(filepath) + ".gz")
            assert compressed_path.exists()
            
            loaded_data = serializer.load(compressed_path)
            assert loaded_data == test_data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_load_nonexistent_file(self):
        """测试加载不存在的文件"""
        serializer = Serializer(format="json")
        result = serializer.load(Path("/nonexistent/path/to/file.json"))
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_serializer_load_invalid_data(self):
        """测试加载无效数据"""
        serializer = Serializer(format="json")
        
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"invalid json data")
            temp_path = Path(f.name)
        
        try:
            result = serializer.load(temp_path)
            assert result is None
        finally:
            os.unlink(temp_path)


class TestGetSerializer:
    """测试获取序列化器函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_serializer_json(self):
        """测试获取JSON序列化器"""
        serializer = get_serializer("json")
        assert isinstance(serializer, Serializer)
        assert serializer.format == "json"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_serializer_pickle(self):
        """测试获取Pickle序列化器"""
        serializer = get_serializer("pickle")
        assert isinstance(serializer, Serializer)
        assert serializer.format == "pickle"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_serializer_unknown(self):
        """测试获取未知格式序列化器"""
        serializer = get_serializer("unknown")
        assert isinstance(serializer, Serializer)
        # 未知格式应该返回JSON序列化器
        assert serializer.format == "json"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_serializer_singleton(self):
        """测试序列化器单例"""
        serializer1 = get_serializer("json")
        serializer2 = get_serializer("json")
        assert serializer1 is serializer2


class TestBenchmark:
    """测试性能基准测试"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_benchmark_serialization(self):
        """测试序列化性能基准测试"""
        test_data = {
            "items": [{"id": f"item_{i}", "content": f"测试内容 {i}"} for i in range(10)],
            "total": 10,
            "version": "1.0"
        }
        
        results = benchmark_serialization(test_data, iterations=10)
        assert isinstance(results, dict)
        assert "json" in results
        assert "pickle" in results


class TestSerializationConstants:
    """测试序列化常量"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_has_msgpack(self):
        """测试MsgPack可用性"""
        assert isinstance(HAS_MSGPACK, bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_has_cbor(self):
        """测试CBOR可用性"""
        assert isinstance(HAS_CBOR, bool)