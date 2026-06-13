"""
V2 功能性能基准测试
"""

import pytest
import time
import tempfile
from pathlib import Path

from agent.digital_life_v2 import DigitalLifeV2


class TestV2Performance:
    """V2 性能基准测试"""
    
    @pytest.fixture
    def temp_data_dir(self):
        """创建临时数据目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def v2_instance(self, temp_data_dir):
        """创建 V2 实例"""
        config = {
            "distillation": {
                "enabled": True,
                "interval": 10,
                "data_dir": str(temp_data_dir / "persona"),
                "distiller_enabled": True,
            },
        }
        return DigitalLifeV2(config)
    
    @pytest.mark.benchmark
    @pytest.mark.v2
    def test_v2_initialization_performance(self, v2_instance):
        """测试 V2 初始化性能"""
        start_time = time.time()
        
        # 启动和停止
        v2_instance.start()
        v2_instance.stop()
        
        elapsed = time.time() - start_time
        
        print(f"V2 初始化和启停耗时: {elapsed:.3f}s")
        # 不设硬性阈值，只是记录性能
    
    @pytest.mark.benchmark
    @pytest.mark.v2
    def test_get_status_performance(self, v2_instance):
        """测试获取状态报告性能"""
        v2_instance.start()
        
        start_time = time.time()
        for _ in range(10):
            v2_instance.get_status()
        elapsed = time.time() - start_time
        
        print(f"获取状态报告 10 次耗时: {elapsed:.3f}s")
        v2_instance.stop()
    
    @pytest.mark.benchmark
    @pytest.mark.v2
    def test_chat_response_performance(self, v2_instance):
        """测试对话响应性能"""
        v2_instance.start()
        
        test_messages = [
            "你好",
            "今天天气怎么样",
            "你是谁",
            "你能做什么",
            "说个笑话",
        ]
        
        start_time = time.time()
        for msg in test_messages:
            v2_instance.chat(msg)
        elapsed = time.time() - start_time
        
        print(f"处理 {len(test_messages)} 条消息耗时: {elapsed:.3f}s")
        v2_instance.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
