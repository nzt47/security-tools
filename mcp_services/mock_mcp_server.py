"""MCP响应模拟器 - 用于本地测试Windows子进程通信逻辑

这个模块提供模拟的MCP服务响应，用于：
1. 测试MCP客户端的Windows兼容性
2. 验证重试机制和超时处理
3. 调试JSON-RPC通信协议
4. 模拟各种异常场景

使用方法：
    # 模拟正常响应
    python -c "from mcp_mock_server import start_mock_server; start_mock_server()"

    # 模拟延迟响应（测试超时）
    python -c "from mcp_mock_server import start_slow_mock_server; start_slow_mock_server()"

    # 模拟错误响应
    python -c "from mcp_mock_server import start_error_mock_server; start_error_mock_server()"
"""

import json
import sys
import time
import random
from typing import Dict, Any, List, Optional

# ════════════════════════════════════════════════════════════════════
# 模拟MCP响应数据
# ════════════════════════════════════════════════════════════════════

class MockMCPResponse:
    """模拟MCP响应数据生成器"""

    # 服务器信息
    SERVER_INFO = {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"listChanged": True},
            "prompts": {"listChanged": True},
        },
        "serverInfo": {
            "name": "mock-multi-search-engine",
            "version": "1.0.0",
            "description": "模拟多引擎搜索MCP服务",
        },
        "instructions": "这是一个用于测试的模拟MCP服务"
    }

    # 工具列表
    TOOLS_LIST = {
        "tools": [
            {
                "name": "search",
                "description": "执行多引擎搜索，支持17个搜索引擎",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "engines": {"type": "array", "items": {"type": "string"}},
                        "num_results": {"type": "number", "default": 10},
                        "language": {"type": "string", "enum": ["en", "zh", "all"]},
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "get_engines",
                "description": "获取支持的搜索引擎列表",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "get_stats",
                "description": "获取搜索使用统计",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ]
    }

    # 搜索引擎元数据
    ENGINES = {
        "baidu": {"name": "百度", "language": "zh", "need_key": False, "category": "chinese"},
        "sogou": {"name": "搜狗", "language": "zh", "need_key": False, "category": "chinese"},
        "360": {"name": "360搜索", "language": "zh", "need_key": False, "category": "chinese"},
        "google": {"name": "Google", "language": "en", "need_key": True, "category": "global"},
        "bing": {"name": "Bing", "language": "en", "need_key": True, "category": "global"},
    }

    @staticmethod
    def generate_search_result(query: str, engines: List[str], num_results: int) -> Dict:
        """生成模拟搜索结果"""
        results = []
        for engine in engines[:5]:  # 限制最多5个引擎
            engine_meta = MockMCPResponse.ENGINES.get(engine, {"name": engine, "language": "zh"})
            results.append({
                "engine": engine,
                "engine_name": engine_meta["name"],
                "language": engine_meta["language"],
                "category": engine_meta["category"],
                "query": query,
                "num_results": min(num_results, 5),
                "results": [
                    {
                        "title": f"[{engine}] {query} - 结果 {i+1}",
                        "url": f"https://example.com/{engine}/result_{i+1}",
                        "snippet": f"这是关于 '{query}' 的第 {i+1} 条搜索结果，来自 {engine_meta['name']}。内容包含相关性和时效性信息。",
                        "source": engine,
                        "score": round(random.uniform(0.7, 0.99), 2)
                    }
                    for i in range(min(num_results, 5))
                ],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

        return {
            "ok": True,
            "query": query,
            "engines_used": engines[:5],
            "total_engines": min(len(engines), 5),
            "results": results,
            "stats": {
                "total_searches": random.randint(100, 1000),
                "engine_usage": {e: random.randint(10, 100) for e in engines[:5]}
            }
        }

    @staticmethod
    def generate_engines_response() -> Dict:
        """生成引擎列表响应"""
        return {
            "ok": True,
            "engines": MockMCPResponse.ENGINES
        }

    @staticmethod
    def generate_stats_response() -> Dict:
        """生成统计响应"""
        return {
            "ok": True,
            "stats": {
                "total_searches": random.randint(500, 5000),
                "engine_usage": {
                    "baidu": random.randint(100, 500),
                    "sogou": random.randint(80, 400),
                    "360": random.randint(50, 300),
                    "google": random.randint(200, 800),
                    "bing": random.randint(150, 600),
                },
                "avg_response_time_ms": random.randint(50, 200),
                "uptime_seconds": random.randint(3600, 86400),
            }
        }


# ════════════════════════════════════════════════════════════════════
# MCP请求处理器
# ════════════════════════════════════════════════════════════════════

class MockMCPHandler:
    """模拟MCP协议处理器"""

    def __init__(self, delay_ms: int = 0, error_rate: float = 0.0):
        """
        Args:
            delay_ms: 响应延迟（毫秒）
            error_rate: 错误率 (0.0-1.0)
        """
        self.delay_ms = delay_ms
        self.error_rate = error_rate
        self.request_count = 0

    def _maybe_delay(self):
        """模拟处理延迟"""
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000)

    def _maybe_error(self) -> bool:
        """根据错误率决定是否返回错误"""
        return random.random() < self.error_rate

    def handle_request(self, request: Dict) -> Dict:
        """处理MCP请求"""
        self.request_count += 1
        method = request.get("method", "")
        req_id = request.get("id")

        print(f"[模拟MCP] 请求 #{self.request_count}: method={method}, id={req_id}", flush=True)

        # 模拟延迟
        self._maybe_delay()

        # 模拟错误
        if self._maybe_error():
            print(f"[模拟MCP] 模拟错误响应", flush=True)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": "模拟的内部错误"}
            }

        # 路由处理
        try:
            if method == "initialize":
                result = MockMCPResponse.SERVER_INFO
            elif method == "tools/list":
                result = MockMCPResponse.TOOLS_LIST
            elif method == "tools/call":
                result = self._handle_tools_call(request.get("params", {}))
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "ping":
                result = {"pong": True}
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"方法不存在: {method}"}
                }

            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        except Exception as e:
            print(f"[模拟MCP] 处理请求失败: {e}", flush=True)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"内部错误: {e}"}
            }

    def _handle_tools_call(self, params: Dict) -> Dict:
        """处理工具调用"""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        print(f"[模拟MCP] 调用工具: {tool_name}, 参数: {arguments}", flush=True)

        if tool_name == "search":
            result = MockMCPResponse.generate_search_result(
                query=arguments.get("query", ""),
                engines=arguments.get("engines", ["baidu"]),
                num_results=arguments.get("num_results", 5)
            )
        elif tool_name == "get_engines":
            result = MockMCPResponse.generate_engines_response()
        elif tool_name == "get_stats":
            result = MockMCPResponse.generate_stats_response()
        else:
            raise ValueError(f"未知工具: {tool_name}")

        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": not result.get("ok", True)
        }


# ════════════════════════════════════════════════════════════════════
# 模拟服务器启动函数
# ════════════════════════════════════════════════════════════════════

def start_mock_server(delay_ms: int = 0, error_rate: float = 0.0):
    """启动模拟MCP服务器（正常模式）

    Args:
        delay_ms: 响应延迟（毫秒）
        error_rate: 错误率 (0.0-1.0)
    """
    print("=" * 60, flush=True)
    print("启动模拟MCP服务器 - 正常模式", flush=True)
    print(f"  延迟: {delay_ms}ms", flush=True)
    print(f"  错误率: {error_rate * 100:.1f}%", flush=True)
    print("=" * 60, flush=True)

    handler = MockMCPHandler(delay_ms=delay_ms, error_rate=error_rate)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            response = handler.handle_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"解析错误: {e}"}
            }, ensure_ascii=False), flush=True)


def start_slow_mock_server(delay_seconds: float = 5.0):
    """启动模拟MCP服务器（慢响应模式，测试超时）

    Args:
        delay_seconds: 响应延迟（秒）
    """
    print("=" * 60, flush=True)
    print("启动模拟MCP服务器 - 慢响应模式", flush=True)
    print(f"  延迟: {delay_seconds}秒（用于测试超时）", flush=True)
    print("=" * 60, flush=True)

    handler = MockMCPHandler()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            print(f"[模拟MCP] 收到请求，等待 {delay_seconds} 秒...", flush=True)
            time.sleep(delay_seconds)  # 模拟慢响应
            response = handler.handle_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"解析错误: {e}"}
            }, ensure_ascii=False), flush=True)


def start_error_mock_server(error_type: str = "random"):
    """启动模拟MCP服务器（错误模式）

    Args:
        error_type: 错误类型 ("random", "timeout", "json_error", "server_error")
    """
    print("=" * 60, flush=True)
    print("启动模拟MCP服务器 - 错误模式", flush=True)
    print(f"  错误类型: {error_type}", flush=True)
    print("=" * 60, flush=True)

    handler = MockMCPHandler()

    error_count = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            error_count += 1

            if error_type == "random":
                # 随机错误
                if error_count % 3 == 0:
                    raise Exception("随机模拟错误")
            elif error_type == "timeout":
                # 周期性超时
                if error_count % 2 == 0:
                    time.sleep(300)  # 模拟超时
            elif error_type == "json_error":
                # JSON格式错误
                if error_count % 4 == 0:
                    print("这不是有效的JSON}", flush=True)
                    continue
            elif error_type == "server_error":
                # 服务器内部错误
                if error_count % 5 == 0:
                    raise RuntimeError("模拟的服务器内部错误")

            response = handler.handle_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)

        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"解析错误: {e}"}
            }, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": f"内部错误: {e}"}
            }, ensure_ascii=False), flush=True)


def start_batch_test_server():
    """启动批量测试模式服务器"""
    print("=" * 60, flush=True)
    print("启动模拟MCP服务器 - 批量测试模式", flush=True)
    print("=" * 60, flush=True)

    handler = MockMCPHandler(delay_ms=100)

    test_scenarios = [
        # 场景1: 正常搜索
        {
            "name": "正常搜索",
            "request": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"}
            }
        },
        {
            "name": "工具列表",
            "request": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
        },
        {
            "name": "执行搜索",
            "request": {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": "测试查询", "engines": ["baidu", "sogou"], "num_results": 3}
                }
            }
        },
        {
            "name": "获取引擎",
            "request": {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_engines", "arguments": {}}
            }
        },
        {
            "name": "获取统计",
            "request": {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "get_stats", "arguments": {}}
            }
        },
    ]

    # 模拟批量请求
    for scenario in test_scenarios:
        print(f"\n[批量测试] 发送: {scenario['name']}", flush=True)
        response = handler.handle_request(scenario["request"])
        print(f"[批量测试] 响应: {json.dumps(response, ensure_ascii=False)[:200]}...", flush=True)
        time.sleep(0.5)

    print("\n" + "=" * 60, flush=True)
    print("批量测试完成！", flush=True)
    print("=" * 60, flush=True)


# ════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCP响应模拟器")
    parser.add_argument("--mode", "-m", choices=["normal", "slow", "error", "batch"],
                       default="normal", help="服务器模式")
    parser.add_argument("--delay", "-d", type=int, default=0,
                       help="响应延迟（毫秒）")
    parser.add_argument("--error-rate", "-e", type=float, default=0.0,
                       help="错误率 (0.0-1.0)")
    parser.add_argument("--error-type", "-t", choices=["random", "timeout", "json_error", "server_error"],
                       default="random", help="错误类型")
    parser.add_argument("--slow-delay", "-s", type=float, default=5.0,
                       help="慢响应延迟（秒）")

    args = parser.parse_args()

    if args.mode == "normal":
        start_mock_server(delay_ms=args.delay, error_rate=args.error_rate)
    elif args.mode == "slow":
        start_slow_mock_server(delay_seconds=args.slow_delay)
    elif args.mode == "error":
        start_error_mock_server(error_type=args.error_type)
    elif args.mode == "batch":
        start_batch_test_server()