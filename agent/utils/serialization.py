
"""
高性能序列化工具
支持多种序列化格式的优化版本
"""

import logging
import json
import pickle
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)
logger.info("[Serialization] 加载序列化工具")

# 检测可用的序列化库
HAS_MSGPACK = False
HAS_CBOR = False

try:
    import msgpack
    HAS_MSGPACK = True
    logger.info("[Serialization] msgpack 可用")
except ImportError:
    logger.warning("[Serialization] msgpack 不可用")

try:
    import cbor2
    HAS_CBOR = True
    logger.info("[Serialization] cbor2 可用")
except ImportError:
    logger.warning("[Serialization] cbor2 不可用")


class Serializer:
    """
    高性能序列化器
    
    支持的格式:
    - json: 标准 JSON (兼容性最好)
    - pickle: Python pickle (速度快, 仅 Python)
    - msgpack: MessagePack (二进制格式, 性能好)
    - cbor: CBOR (二进制格式, 性能好)
    """
    
    def __init__(self, format: str = "json", compress: bool = False):
        """
        初始化序列化器
        
        Args:
            format: 序列化格式 (json/pickle/msgpack/cbor)
            compress: 是否压缩
        """
        self.format = format.lower()
        self.compress = compress
        
        # 选择序列化方法
        if self.format == "json":
            self.serialize = self._serialize_json
            self.deserialize = self._deserialize_json
        elif self.format == "pickle":
            self.serialize = self._serialize_pickle
            self.deserialize = self._deserialize_pickle
        elif self.format == "msgpack":
            if not HAS_MSGPACK:
                logger.warning("[Serialization] msgpack 不可用, 降级为 json")
                self.format = "json"
                self.serialize = self._serialize_json
                self.deserialize = self._deserialize_json
            else:
                self.serialize = self._serialize_msgpack
                self.deserialize = self._deserialize_msgpack
        elif self.format == "cbor":
            if not HAS_CBOR:
                logger.warning("[Serialization] cbor 不可用, 降级为 json")
                self.format = "json"
                self.serialize = self._serialize_json
                self.deserialize = self._deserialize_json
            else:
                self.serialize = self._serialize_cbor
                self.deserialize = self._deserialize_cbor
        else:
            logger.warning(f"[Serialization] 未知格式: {format}, 使用 json")
            self.format = "json"
            self.serialize = self._serialize_json
            self.deserialize = self._deserialize_json
        
        logger.info(f"[Serialization] 初始化完成: format={self.format}, compress={compress}")
    
    def _serialize_json(self, data: Any) -> bytes:
        """JSON 序列化"""
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    
    def _deserialize_json(self, data: bytes) -> Any:
        """JSON 反序列化"""
        return json.loads(data.decode("utf-8"))
    
    def _serialize_pickle(self, data: Any) -> bytes:
        """Pickle 序列化"""
        return pickle.dumps(data)
    
    def _deserialize_pickle(self, data: bytes) -> Any:
        """Pickle 反序列化"""
        return pickle.loads(data)
    
    def _serialize_msgpack(self, data: Any) -> bytes:
        """MessagePack 序列化"""
        return msgpack.packb(data, use_bin_type=True)
    
    def _deserialize_msgpack(self, data: bytes) -> Any:
        """MessagePack 反序列化"""
        return msgpack.unpackb(data, raw=False)
    
    def _serialize_cbor(self, data: Any) -> bytes:
        """CBOR 序列化"""
        return cbor2.dumps(data)
    
    def _deserialize_cbor(self, data: bytes) -> Any:
        """CBOR 反序列化"""
        return cbor2.loads(data)
    
    def dumps(self, data: Any) -> bytes:
        """
        序列化数据
        
        Args:
            data: 要序列化的数据
            
        Returns:
            序列化的字节串
        """
        return self.serialize(data)
    
    def loads(self, data: bytes) -> Any:
        """
        反序列化数据
        
        Args:
            data: 序列化的字节串
            
        Returns:
            反序列化后的数据
        """
        return self.deserialize(data)
    
    def dump(self, data: Any, filepath: Path) -> None:
        """
        序列化并保存到文件
        
        Args:
            data: 要保存的数据
            filepath: 文件路径
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        serialized = self.dumps(data)
        
        if self.compress:
            import gzip
            filepath = Path(str(filepath) + ".gz")
            with gzip.open(filepath, "wb") as f:
                f.write(serialized)
        else:
            with open(filepath, "wb") as f:
                f.write(serialized)
        
        logger.debug(f"[Serialization] 保存文件: {filepath} ({len(serialized)} bytes)")
    
    def load(self, filepath: Path) -> Optional[Any]:
        """
        从文件加载并反序列化
        
        Args:
            filepath: 文件路径
            
        Returns:
            反序列化后的数据，失败返回 None
        """
        filepath = Path(filepath)
        
        if not filepath.exists():
            logger.debug(f"[Serialization] 文件不存在: {filepath}")
            return None
        
        try:
            if str(filepath).endswith(".gz"):
                import gzip
                with gzip.open(filepath, "rb") as f:
                    data = f.read()
            else:
                with open(filepath, "rb") as f:
                    data = f.read()
            
            return self.loads(data)
        except Exception as e:
            logger.error(f"[Serialization] 加载失败: {filepath}, 错误: {e}")
            return None


# 预定义的序列化器实例
_json_serializer = Serializer(format="json")
_pickle_serializer = Serializer(format="pickle")
_msgpack_serializer = Serializer(format="msgpack") if HAS_MSGPACK else _json_serializer
_cbor_serializer = Serializer(format="cbor") if HAS_CBOR else _json_serializer


def get_serializer(format: str = "json") -> Serializer:
    """
    获取序列化器实例
    
    Args:
        format: 序列化格式
        
    Returns:
        Serializer 实例
    """
    if format == "json":
        return _json_serializer
    elif format == "pickle":
        return _pickle_serializer
    elif format == "msgpack":
        return _msgpack_serializer
    elif format == "cbor":
        return _cbor_serializer
    else:
        return _json_serializer


def benchmark_serialization(data: Dict[str, Any], iterations: int = 100) -> Dict[str, Any]:
    """
    序列化性能基准测试
    
    Args:
        data: 测试数据
        iterations: 迭代次数
        
    Returns:
        性能对比结果
    """
    import time
    
    results = {}
    
    formats = ["json", "pickle"]
    if HAS_MSGPACK:
        formats.append("msgpack")
    if HAS_CBOR:
        formats.append("cbor")
    
    for fmt in formats:
        serializer = Serializer(format=fmt)
        
        # 序列化测试
        t0 = time.time()
        for _ in range(iterations):
            serialized = serializer.dumps(data)
        serialize_time = (time.time() - t0) * 1000
        
        # 反序列化测试
        t1 = time.time()
        for _ in range(iterations):
            serializer.loads(serialized)
        deserialize_time = (time.time() - t1) * 1000
        
        results[fmt] = {
            "serialize_time_ms": round(serialize_time / iterations, 3),
            "deserialize_time_ms": round(deserialize_time / iterations, 3),
            "size_bytes": len(serialized),
            "total_time_ms": round((serialize_time + deserialize_time) / iterations, 3)
        }
        
        logger.info(f"[Serialization] {fmt}: {results[fmt]}")
    
    return results


if __name__ == "__main__":
    # 测试序列化器
    test_data = {
        "items": [
            {"id": f"item_{i}", "content": f"测试内容 {i}" * 10, "metadata": {'index': i}}
            for i in range(100)
        ],
        "total": 100,
        "version": "1.0"
    }
    
    print("=" * 80)
    print("  🚀 序列化性能基准测试")
    print("=" * 80)
    
    results = benchmark_serialization(test_data, iterations=100)
    
    print("\n性能对比:")
    print("-" * 80)
    print(f"{'格式':<12} {'序列化(ms)':<15} {'反序列化(ms)':<15} {'大小(bytes)':<15} {'总耗时(ms)':<15}")
    print("-" * 80)
    
    for fmt, data in sorted(results.items(), key=lambda x: x[1]["total_time_ms"]):
        print(f"{fmt:<12} {data['serialize_time_ms']:<15.3f} {data['deserialize_time_ms']:<15.3f} {data['size_bytes']:<15} {data['total_time_ms']:<15.3f}")
    
    print("=" * 80)