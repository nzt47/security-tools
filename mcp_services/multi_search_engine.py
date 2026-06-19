"""MCP多引擎搜索服务 — 包装Hermes/OpenClaw的multi-search-engine技能

这是一个符合MCP (Model Context Protocol) 标准的搜索服务实现，
可将外部技能包装为云枢可调用的MCP工具。

MCP协议核心特性：
- STDIO通信：进程间标准输入输出
- JSON-RPC 2.0：请求/响应格式
- 工具注册：定义可调用工具列表
- 资源访问：提供可访问的数据资源
"""

import sys
import json
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("multi_search_mcp")

# ════════════════════════════════════════════════════════════════════
# MCP协议基础类型定义
# ════════════════════════════════════════════════════════════════════

@dataclass
class MCPRequest:
    """MCP请求封装"""
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str = ""
    params: Optional[Dict] = None

@dataclass
class MCPResponse:
    """MCP响应封装"""
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    result: Optional[Any] = None
    error: Optional[Dict] = None

# ════════════════════════════════════════════════════════════════════
# 多引擎搜索实现（模拟Hermes multi-search-engine技能）
# ════════════════════════════════════════════════════════════════════

class MultiSearchEngine:
    """多引擎搜索服务 - Hermes/OpenClaw风格

    支持的搜索引擎（17个）：
    - 全球引擎: Google, Bing, DuckDuckGo, Brave, Yahoo, Ask, AOL, WolframAlpha
    - 中国引擎: Baidu, Sogou, 360, So, GoogleCN, WikipediaCN, BingCN, Douban, Zhihu
    """

    # 支持的搜索引擎元数据
    ENGINES = {
        # 全球引擎
        "google": {"name": "Google", "language": "en", "need_key": True, "category": "global"},
        "bing": {"name": "Bing", "language": "en", "need_key": True, "category": "global"},
        "duckduckgo": {"name": "DuckDuckGo", "language": "en", "need_key": False, "category": "global"},
        "brave": {"name": "Brave", "language": "en", "need_key": True, "category": "global"},
        "yahoo": {"name": "Yahoo", "language": "en", "need_key": False, "category": "global"},
        "ask": {"name": "Ask", "language": "en", "need_key": False, "category": "global"},
        "aol": {"name": "AOL", "language": "en", "need_key": False, "category": "global"},
        "wolframalpha": {"name": "WolframAlpha", "language": "en", "need_key": True, "category": "global"},
        # 中国引擎
        "baidu": {"name": "百度", "language": "zh", "need_key": False, "category": "chinese"},
        "sogou": {"name": "搜狗", "language": "zh", "need_key": False, "category": "chinese"},
        "360": {"name": "360搜索", "language": "zh", "need_key": False, "category": "chinese"},
        "so": {"name": "So搜索", "language": "zh", "need_key": False, "category": "chinese"},
        "googlecn": {"name": "Google中国", "language": "zh", "need_key": True, "category": "chinese"},
        "wikipedia_cn": {"name": "维基百科中文", "language": "zh", "need_key": False, "category": "chinese"},
        "bing_cn": {"name": "必应中国", "language": "zh", "need_key": True, "category": "chinese"},
        "douban": {"name": "豆瓣", "language": "zh", "need_key": False, "category": "chinese"},
        "zhihu": {"name": "知乎", "language": "zh", "need_key": False, "category": "chinese"},
    }

    def __init__(self):
        self.stats = {"total_searches": 0, "engine_usage": {}}

    def search(
        self,
        query: str,
        engines: Optional[List[str]] = None,
        num_results: int = 10,
        language: Optional[str] = None,
        site: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """执行多引擎搜索

        Args:
            query: 搜索关键词
            engines: 要使用的引擎列表，默认全部
            num_results: 每引擎返回结果数
            language: 语言过滤 (en/zh/all)
            site: 站点限制 (如 "site:github.com")

        Returns:
            统一的搜索结果格式
        """
        self.stats["total_searches"] += 1

        # 确定要使用的引擎
        if engines:
            target_engines = [e for e in engines if e in self.ENGINES]
        elif language:
            if language == "zh":
                target_engines = [e for e, m in self.ENGINES.items() if m["language"] == "zh"]
            elif language == "en":
                target_engines = [e for e, m in self.ENGINES.items() if m["language"] == "en"]
            else:
                target_engines = list(self.ENGINES.keys())
        else:
            target_engines = list(self.ENGINES.keys())

        # 执行搜索（这里模拟，实际会调用各引擎API）
        results = []
        for engine in target_engines:
            engine_meta = self.ENGINES[engine]

            # 记录引擎使用统计
            self.stats["engine_usage"][engine] = self.stats["engine_usage"].get(engine, 0) + 1

            # 模拟搜索结果（实际实现需调用真实API）
            results.append({
                "engine": engine,
                "engine_name": engine_meta["name"],
                "language": engine_meta["language"],
                "category": engine_meta["category"],
                "need_key": engine_meta["need_key"],
                "query": query,
                "num_results": num_results,
                "results": self._mock_search_results(engine, query, num_results),
                "timestamp": datetime.now().isoformat(),
            })

        return {
            "ok": True,
            "query": query,
            "engines_used": target_engines,
            "total_engines": len(target_engines),
            "results": results,
            "stats": self.stats,
        }

    def _mock_search_results(self, engine: str, query: str, num: int) -> List[Dict]:
        """模拟搜索结果（实际实现需调用真实API）"""
        return [
            {
                "title": f"[{engine}] {query} - 结果 {i+1}",
                "url": f"https://example.com/{engine}/result_{i+1}",
                "snippet": f"这是关于 '{query}' 的第 {i+1} 条搜索结果，来自 {engine} 引擎。",
                "source": engine,
            }
            for i in range(min(num, 5))
        ]

    def get_engines(self) -> Dict[str, Dict]:
        """获取支持的引擎列表"""
        return self.ENGINES

    def get_stats(self) -> Dict:
        """获取搜索统计"""
        return self.stats


# ════════════════════════════════════════════════════════════════════
# MCP协议处理器
# ════════════════════════════════════════════════════════════════════

class MCPProtocolHandler:
    """MCP协议处理器

    实现MCP标准协议：
    - initialize: 初始化连接
    - tools/list: 列出可用工具
    - tools/call: 调用工具
    - resources/list: 列出可用资源
    - resources/read: 读取资源
    """

    def __init__(self):
        self.search_service = MultiSearchEngine()
        self.capabilities = {
            "tools": {"listChanged": True},
            "resources": {"listChanged": True},
            "prompts": {"listChanged": True},
        }

    def handle_request(self, request: Dict) -> Dict:
        """处理MCP请求"""
        try:
            method = request.get("method", "")
            req_id = request.get("id")
            params = request.get("params", {})

            logger.info(f"[MCP] 收到请求: method={method}, id={req_id}")

            # 路由到具体处理函数
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list(params)
            elif method == "tools/call":
                result = self._handle_tools_call(params)
            elif method == "resources/list":
                result = self._handle_resources_list(params)
            elif method == "resources/read":
                result = self._handle_resources_read(params)
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
            logger.error(f"[MCP] 处理请求失败: {e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32603, "message": f"内部错误: {str(e)}"}
            }

    def _handle_initialize(self, params: Dict) -> Dict:
        """处理初始化请求"""
        logger.info("[MCP] 初始化连接...")
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": self.capabilities,
            "serverInfo": {
                "name": "multi-search-engine-mcp",
                "version": "1.0.0",
                "description": "Hermes多引擎搜索技能MCP服务",
            },
            "instructions": """
# Multi-Search-Engine MCP服务

这是一个封装了Hermes/OpenClaw多引擎搜索技能的MCP服务。

## 可用工具

### search
执行多引擎搜索，支持17个搜索引擎。

**参数：**
- query (string, 必需): 搜索关键词
- engines (string[]): 要使用的引擎列表
- num_results (number): 每引擎返回结果数，默认10
- language (string): 语言过滤 (en/zh/all)
- site (string): 站点限制

**示例：**
```
搜索"AI新闻"，使用百度和搜狗：
{"query": "AI新闻", "engines": ["baidu", "sogou"], "num_results": 5}

搜索英文内容，使用Google和Bing：
{"query": "machine learning", "language": "en", "engines": ["google", "bing"]}
```

### get_engines
获取支持的搜索引擎列表。

### get_stats
获取搜索使用统计。
            """.strip(),
        }

    def _handle_tools_list(self, params: Dict) -> Dict:
        """处理工具列表请求"""
        return {
            "tools": [
                {
                    "name": "search",
                    "description": "执行多引擎搜索，支持17个搜索引擎（Google、Bing、百度、搜狗等）",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词"
                            },
                            "engines": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "要使用的引擎列表，如 [\"baidu\", \"sogou\"]"
                            },
                            "num_results": {
                                "type": "number",
                                "description": "每引擎返回结果数，默认10",
                                "default": 10
                            },
                            "language": {
                                "type": "string",
                                "enum": ["en", "zh", "all"],
                                "description": "语言过滤"
                            },
                            "site": {
                                "type": "string",
                                "description": "站点限制，如 \"site:github.com\""
                            }
                        },
                        "required": ["query"]
                    }
                },
                {
                    "name": "get_engines",
                    "description": "获取支持的搜索引擎列表和元数据",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "get_stats",
                    "description": "获取搜索使用统计",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }

    def _handle_tools_call(self, params: Dict) -> Dict:
        """处理工具调用请求"""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        logger.info(f"[MCP] 调用工具: {tool_name}, 参数: {arguments}")

        if tool_name == "search":
            result = self.search_service.search(**arguments)
        elif tool_name == "get_engines":
            result = {"ok": True, "engines": self.search_service.get_engines()}
        elif tool_name == "get_stats":
            result = {"ok": True, "stats": self.search_service.get_stats()}
        else:
            raise ValueError(f"未知工具: {tool_name}")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2)
                }
            ],
            "isError": not result.get("ok", True)
        }

    def _handle_resources_list(self, params: Dict) -> Dict:
        """处理资源列表请求"""
        return {
            "resources": [
                {
                    "uri": "search://engines",
                    "name": "搜索引擎列表",
                    "description": "所有支持的搜索引擎及其元数据",
                    "mimeType": "application/json"
                },
                {
                    "uri": "search://stats",
                    "name": "搜索统计",
                    "description": "搜索使用统计信息",
                    "mimeType": "application/json"
                }
            ]
        }

    def _handle_resources_read(self, params: Dict) -> Dict:
        """处理资源读取请求"""
        uri = params.get("uri")

        if uri == "search://engines":
            content = json.dumps(self.search_service.get_engines(), ensure_ascii=False)
        elif uri == "search://stats":
            content = json.dumps(self.search_service.get_stats(), ensure_ascii=False)
        else:
            raise ValueError(f"未知资源: {uri}")

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": content
                }
            ]
        }


# ════════════════════════════════════════════════════════════════════
# STDIO通信主循环
# ════════════════════════════════════════════════════════════════════

def main():
    """MCP服务主入口 - STDIO通信模式"""
    logger.info("[MCP] Multi-Search-Engine服务启动 (STDIO模式)")

    handler = MCPProtocolHandler()

    # STDIO主循环：读取JSON-RPC请求，返回JSON-RPC响应
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            response = handler.handle_request(request)

            # 输出响应（带换行符）
            print(json.dumps(response, ensure_ascii=False), flush=True)

        except json.JSONDecodeError as e:
            logger.error(f"[MCP] JSON解析失败: {e}")
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"解析错误: {e}"}
            }), flush=True)
        except Exception as e:
            logger.error(f"[MCP] 处理失败: {e}", exc_info=True)
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": f"内部错误: {e}"}
            }), flush=True)


if __name__ == "__main__":
    main()
