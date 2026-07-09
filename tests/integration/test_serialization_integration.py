"""serialization.py 集成测试

覆盖序列化工具的核心功能：
1. JSON/Pickle 格式往返
2. msgpack/cbor 可用性降级
3. 未知格式降级
4. 文件读写（含压缩）
5. 工厂函数
6. 基准测试
"""

import pytest
from pathlib import Path

from agent.utils.serialization import (
    Serializer,
    get_serializer,
    benchmark_serialization,
    HAS_MSGPACK,
    HAS_CBOR,
)

pytestmark = pytest.mark.integration


class TestSerializationIntegration:
    """序列化工具集成测试"""

    def test_json_serialize_deserialize(self):
        """测试 1：JSON 格式往返"""
        ser = Serializer(format="json")
        data = {"name": "测试", "value": 42, "nested": {"list": [1, 2, 3]}}

        serialized = ser.dumps(data)
        assert isinstance(serialized, bytes)

        deserialized = ser.loads(serialized)
        assert deserialized == data
        assert deserialized["name"] == "测试"
        assert deserialized["nested"]["list"] == [1, 2, 3]

    def test_pickle_serialize_deserialize(self):
        """测试 2：Pickle 格式往返"""
        ser = Serializer(format="pickle")
        data = {"items": [1, 2, 3], "tuple": (4, 5), "set": {6, 7}}

        serialized = ser.dumps(data)
        deserialized = ser.loads(serialized)
        assert deserialized == data

    def test_msgpack_format(self):
        """测试 3：msgpack 往返或降级为 json"""
        ser = Serializer(format="msgpack")
        data = {"key": "value", "num": 123}

        serialized = ser.dumps(data)
        deserialized = ser.loads(serialized)
        assert deserialized == data

        # 验证格式标记
        if HAS_MSGPACK:
            assert ser.format == "msgpack"
        else:
            assert ser.format == "json"  # 降级

    def test_cbor_format(self):
        """测试 4：cbor 往返或降级为 json"""
        ser = Serializer(format="cbor")
        data = {"key": "value", "num": 123}

        serialized = ser.dumps(data)
        deserialized = ser.loads(serialized)
        assert deserialized == data

        if HAS_CBOR:
            assert ser.format == "cbor"
        else:
            assert ser.format == "json"  # 降级

    def test_unknown_format_fallbacks_to_json(self):
        """测试 5：未知格式降级为 json"""
        ser = Serializer(format="xml")
        assert ser.format == "json"

        data = {"test": True}
        serialized = ser.dumps(data)
        deserialized = ser.loads(serialized)
        assert deserialized == data

    def test_dump_and_load_file(self, tmp_path):
        """测试 6：文件读写"""
        ser = Serializer(format="json")
        data = {"name": "file_test", "values": [10, 20, 30]}

        filepath = tmp_path / "data.json"
        ser.dump(data, filepath)

        assert filepath.exists()
        loaded = ser.load(filepath)
        assert loaded == data

    def test_dump_compressed(self, tmp_path):
        """测试 7：压缩文件读写"""
        ser = Serializer(format="json", compress=True)
        data = {"name": "compressed_test", "content": "A" * 1000}

        filepath = tmp_path / "data.json"
        ser.dump(data, filepath)

        # 压缩后文件名加 .gz 后缀
        compressed_path = Path(str(filepath) + ".gz")
        assert compressed_path.exists()

        # 读取时自动检测 .gz
        loaded = ser.load(compressed_path)
        assert loaded == data

    def test_load_nonexistent_file(self, tmp_path):
        """测试 8：不存在的文件返回 None"""
        ser = Serializer(format="json")
        result = ser.load(tmp_path / "nonexistent.json")
        assert result is None

    def test_get_serializer_returns_correct_instance(self):
        """测试 9：工厂函数返回正确的实例"""
        json_ser = get_serializer("json")
        assert json_ser.format == "json"

        pickle_ser = get_serializer("pickle")
        assert pickle_ser.format == "pickle"

        # 未知格式返回 json
        unknown_ser = get_serializer("unknown")
        assert unknown_ser.format == "json"

        # 相同格式返回同一实例（单例缓存）
        assert get_serializer("json") is json_ser

    def test_benchmark_serialization(self):
        """测试 10：基准测试返回结果"""
        data = {"items": [{"id": i, "content": f"test {i}"} for i in range(10)]}
        results = benchmark_serialization(data, iterations=10)

        assert "json" in results
        assert "pickle" in results

        for fmt, metrics in results.items():
            assert "serialize_time_ms" in metrics
            assert "deserialize_time_ms" in metrics
            assert "size_bytes" in metrics
            assert "total_time_ms" in metrics
            assert metrics["size_bytes"] > 0
            assert metrics["serialize_time_ms"] >= 0

    def test_json_handles_unicode(self):
        """测试 11：JSON 正确处理中文/Emoji"""
        ser = Serializer(format="json")
        data = {
            "chinese": "你好世界",
            "emoji": "🚀🎉",
            "mixed": "Hello 世界 🌍",
        }

        serialized = ser.dumps(data)
        deserialized = ser.loads(serialized)
        assert deserialized == data
        # ensure_ascii=False → 中文不会被转义
        assert "你好世界" in serialized.decode("utf-8")
