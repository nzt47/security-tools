"""multi-search-engine 技能单元测试

覆盖功能：
- search: 多引擎搜索
- get_engines: 获取引擎列表
- get_stats: 获取统计信息

运行方式：
    python -m pytest tests/test_multi_search_engine.py -v
    python tests/test_multi_search_engine.py
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# 添加项目路径（tests目录相对于agent目录）
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)

from mcp_services.multi_search_engine import MultiSearchEngine, MCPProtocolHandler


class TestMultiSearchEngine:
    """多引擎搜索服务单元测试"""

    def setup_method(self):
        """每个测试方法前初始化"""
        self.search_engine = MultiSearchEngine()

    def test_search_with_single_engine(self):
        """测试单引擎搜索"""
        result = self.search_engine.search(query="AI人工智能", engines=["baidu"], num_results=3)
        
        assert result["ok"] is True
        assert result["query"] == "AI人工智能"
        assert result["engines_used"] == ["baidu"]
        assert result["total_engines"] == 1
        
        results = result["results"]
        assert len(results) == 1
        assert results[0]["engine"] == "baidu"
        assert results[0]["engine_name"] == "百度"
        assert len(results[0]["results"]) == 3

    def test_search_with_multiple_engines(self):
        """测试多引擎搜索"""
        result = self.search_engine.search(
            query="Python", 
            engines=["baidu", "bing", "google"], 
            num_results=2
        )
        
        assert result["ok"] is True
        assert result["total_engines"] == 3
        assert set(result["engines_used"]) == {"baidu", "bing", "google"}
        
        results = result["results"]
        assert len(results) == 3
        
        engine_names = {r["engine"] for r in results}
        assert engine_names == {"baidu", "bing", "google"}

    def test_search_with_invalid_engine(self):
        """测试无效引擎处理"""
        result = self.search_engine.search(query="test", engines=["invalid_engine"])
        
        assert result["ok"] is True
        assert result["total_engines"] == 0
        assert result["engines_used"] == []

    def test_search_language_filter(self):
        """测试语言过滤"""
        result = self.search_engine.search(query="test", engines=["baidu", "google"], language="zh")
        
        assert result["ok"] is True
        # 百度是中文引擎，应该被使用
        engines_used = result["engines_used"]
        assert "baidu" in engines_used

    def test_get_engines(self):
        """测试获取引擎列表"""
        engines = self.search_engine.get_engines()
        
        assert isinstance(engines, dict)
        assert len(engines) == 17  # 17个搜索引擎
        
        # 检查必需字段
        for engine_id, meta in engines.items():
            assert "name" in meta
            assert "language" in meta
            assert "category" in meta
            assert "need_key" in meta

    def test_get_stats(self):
        """测试获取统计信息"""
        stats = self.search_engine.get_stats()
        
        assert "total_searches" in stats
        assert "engine_usage" in stats
        assert isinstance(stats["total_searches"], int)
        assert isinstance(stats["engine_usage"], dict)

    def test_stats_update_after_search(self):
        """测试搜索后统计更新"""
        initial_searches = self.search_engine.get_stats()["total_searches"]
        
        # 执行多次搜索
        self.search_engine.search(query="test1", engines=["baidu"])
        self.search_engine.search(query="test2", engines=["bing"])
        self.search_engine.search(query="test3", engines=["baidu"])
        
        stats = self.search_engine.get_stats()
        
        assert stats["total_searches"] == initial_searches + 3
        assert stats["engine_usage"]["baidu"] == 2
        assert stats["engine_usage"]["bing"] == 1


class TestMCPProtocolHandler:
    """MCP协议处理器单元测试"""

    def setup_method(self):
        """每个测试方法前初始化"""
        self.handler = MCPProtocolHandler()

    def test_initialize(self):
        """测试初始化请求"""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        }
        
        response = self.handler.handle_request(request)
        
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert "error" not in response
        
        result = response["result"]
        assert "protocolVersion" in result
        assert "capabilities" in result
        assert "serverInfo" in result

    def test_tools_list(self):
        """测试工具列表请求"""
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        
        response = self.handler.handle_request(request)
        
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 2
        
        tools = response["result"]["tools"]
        assert len(tools) == 3
        
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"search", "get_engines", "get_stats"}

    def test_tools_call_search(self):
        """测试工具调用 - search"""
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {
                    "query": "测试",
                    "engines": ["baidu"],
                    "num_results": 2
                }
            }
        }
        
        response = self.handler.handle_request(request)
        
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 3
        
        result = response["result"]
        assert "content" in result
        assert isinstance(result["content"], list)
        assert len(result["content"]) > 0
        
        content = result["content"][0]
        assert content["type"] == "text"
        
        # 解析返回的JSON内容
        data = json.loads(content["text"])
        assert data["ok"] is True
        assert data["query"] == "测试"

    def test_tools_call_get_engines(self):
        """测试工具调用 - get_engines"""
        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_engines",
                "arguments": {}
            }
        }
        
        response = self.handler.handle_request(request)
        
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 4
        
        result = response["result"]
        content = result["content"][0]
        data = json.loads(content["text"])
        
        assert data["ok"] is True
        assert "engines" in data
        assert len(data["engines"]) == 17

    def test_tools_call_get_stats(self):
        """测试工具调用 - get_stats"""
        request = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_stats",
                "arguments": {}
            }
        }
        
        response = self.handler.handle_request(request)
        
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 5
        
        result = response["result"]
        content = result["content"][0]
        data = json.loads(content["text"])
        
        assert data["ok"] is True
        assert "stats" in data

    def test_unknown_method(self):
        """测试未知方法处理"""
        request = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "unknown_method",
            "params": {}
        }
        
        response = self.handler.handle_request(request)
        
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 6
        assert "error" in response
        assert response["error"]["code"] == -32601


class TestMCPBridgeIntegration:
    """MCP桥接器集成测试"""

    @pytest.mark.asyncio
    async def test_bridge_install_and_call(self):
        """测试桥接器安装和调用流程"""
        from mcp_services.yunshu_mcp_bridge import YunshuMCPBridge
        
        bridge = YunshuMCPBridge()
        
        # 安装服务
        install_result = await bridge.install_service("multi-search-engine")
        assert install_result["ok"] is True
        
        # 调用工具
        search_result = await bridge.call_tool(
            "multi-search-engine",
            "search",
            {"query": "test", "engines": ["baidu"], "num_results": 2}
        )
        
        assert search_result["ok"] is True
        assert search_result["query"] == "test"
        
        # 获取引擎列表
        engines_result = await bridge.call_tool(
            "multi-search-engine",
            "get_engines",
            {}
        )
        
        assert engines_result["ok"] is True
        assert "engines" in engines_result
        
        # 获取统计
        stats_result = await bridge.call_tool(
            "multi-search-engine",
            "get_stats",
            {}
        )
        
        assert stats_result["ok"] is True
        assert "stats" in stats_result
        
        # 停止服务
        stop_result = await bridge.stop_service("multi-search-engine")
        assert stop_result is True


if __name__ == "__main__":
    # 运行所有测试
    pytest.main([__file__, "-v"])
    
    # 或者手动运行测试
    print("=" * 60)
    print("运行 multi-search-engine 单元测试")
    print("=" * 60)
    
    # 测试 MultiSearchEngine
    print("\n[1] 测试 MultiSearchEngine")
    engine = MultiSearchEngine()
    
    print("  - test_search_with_single_engine...", end=" ")
    result = engine.search(query="AI人工智能", engines=["baidu"], num_results=3)
    assert result["ok"] is True
    print("PASS")
    
    print("  - test_search_with_multiple_engines...", end=" ")
    result = engine.search(query="Python", engines=["baidu", "bing"], num_results=2)
    assert result["ok"] is True
    assert len(result["engines_used"]) == 2
    print("PASS")
    
    print("  - test_get_engines...", end=" ")
    engines = engine.get_engines()
    assert len(engines) == 17
    print("PASS")
    
    print("  - test_get_stats...", end=" ")
    stats = engine.get_stats()
    assert "total_searches" in stats
    print("PASS")
    
    # 测试 MCPProtocolHandler
    print("\n[2] 测试 MCPProtocolHandler")
    handler = MCPProtocolHandler()
    
    print("  - test_initialize...", end=" ")
    response = handler.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}
    })
    assert "result" in response
    print("PASS")
    
    print("  - test_tools_list...", end=" ")
    response = handler.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
    })
    assert len(response["result"]["tools"]) == 3
    print("PASS")
    
    print("  - test_tools_call_search...", end=" ")
    response = handler.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": "test", "engines": ["baidu"]}}
    })
    assert response["result"]["content"][0]["type"] == "text"
    print("PASS")
    
    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)