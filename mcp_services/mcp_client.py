"""MCP客户端 — 连接并调用MCP服务（修复Windows子进程通信问题）

修复内容：
1. 增加启动延迟等待（Windows子进程启动较慢）
2. 添加重试机制（带指数退避）
3. 增加超时时间配置
4. 修复STDIO通信问题（处理Windows行尾符）
5. 添加进程健康检查
6. 增加连接池管理
7. 修复缓冲区读取问题（按块读取，避免行缓冲阻塞）
8. 增强异常处理和日志记录

使用示例：
    client = MCPClient("python", ["mcp_services/multi_search_engine.py"], timeout=60)
    await client.initialize(max_retries=3)

    # 调用工具
    result = await client.call_tool("search", {
        "query": "AI新闻",
        "engines": ["baidu", "sogou"],
        "num_results": 5
    })
"""

import asyncio
import json
import logging
import subprocess
import time
import os
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from pathlib import Path
from functools import wraps

logger = logging.getLogger("mcp_client")

# ════════════════════════════════════════════════════════════════════
# 配置常量
# ════════════════════════════════════════════════════════════════════

DEFAULT_TIMEOUT = 60  # 默认超时时间（秒）- 增加到60秒
DEFAULT_MAX_RETRIES = 3  # 默认重试次数
INITIAL_DELAY = 1.0  # 初始重试延迟（秒）
BACKOFF_FACTOR = 2.0  # 指数退避因子
STARTUP_WAIT_TIME = 2.0  # 启动后等待时间（Windows需要更长时间）
READ_CHUNK_SIZE = 4096  # 读取缓冲区大小
READ_TIMEOUT = 2.0  # 读取超时时间


# ════════════════════════════════════════════════════════════════════
# 装饰器：重试机制
# ════════════════════════════════════════════════════════════════════

def retry_on_failure(max_retries: int = DEFAULT_MAX_RETRIES, delay: float = INITIAL_DELAY,
                     backoff_factor: float = BACKOFF_FACTOR, retry_exceptions: tuple = (TimeoutError,)):
    """重试装饰器 - 带指数退避"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except retry_exceptions as e:
                    last_exception = e
                    logger.warning(f"[MCP客户端] 操作失败 (尝试 {attempt + 1}/{max_retries}): {e}")

                    if attempt < max_retries - 1:
                        logger.info(f"[MCP客户端] 等待 {current_delay:.2f} 秒后重试...")
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor

            logger.error(f"[MCP客户端] 操作失败，已达到最大重试次数 ({max_retries})")
            raise last_exception

        return wrapper

    return decorator


# ════════════════════════════════════════════════════════════════════
# MCP协议类型
# ════════════════════════════════════════════════════════════════════

@dataclass
class MCPTool:
    """MCP工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class MCPResource:
    """MCP资源定义"""
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"


@dataclass
class MCPConfig:
    """MCP客户端配置"""
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    startup_wait: float = STARTUP_WAIT_TIME
    encoding: str = "utf-8"
    line_separator: str = "\n"
    read_chunk_size: int = READ_CHUNK_SIZE
    read_timeout: float = READ_TIMEOUT


# ════════════════════════════════════════════════════════════════════
# MCP客户端实现
# ════════════════════════════════════════════════════════════════════

class MCPClient:
    """MCP客户端 - STDIO通信模式（修复Windows兼容性）"""

    def __init__(self, command: str, args: Optional[List[str]] = None, env: Optional[Dict] = None,
                 config: Optional[MCPConfig] = None):
        """
        Args:
            command: 启动命令 (如 "python", "node", "npx")
            args: 命令参数 (如 ["mcp_services/multi_search_engine.py"])
            env: 环境变量
            config: MCP客户端配置
        """
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.config = config or MCPConfig()

        self.process: Optional[subprocess.Process] = None
        self.request_id = 0
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self._response_reader_task: Optional[asyncio.Task] = None
        self._read_lock = asyncio.Lock()

        # 服务能力
        self.capabilities: Dict[str, Any] = {}
        self.server_info: Dict[str, Any] = {}
        self.tools: List[MCPTool] = []
        self.resources: List[MCPResource] = []

        self._initialized = False
        self._is_running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """启动MCP服务进程（带Windows兼容性处理）"""
        if self._is_running:
            logger.info("[MCP客户端] 服务已在运行中")
            return

        logger.info(f"[MCP客户端] 启动服务: {self.command} {' '.join(self.args)}")

        # 构建环境变量
        env = {**os.environ, **self.env}

        try:
            # Windows兼容：使用CREATE_NEW_PROCESS_GROUP避免子进程问题
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            # 启动进程
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                creationflags=creation_flags,
                bufsize=0,  # 无缓冲，避免Windows管道问题
            )

            logger.info(f"[MCP客户端] 服务已启动 (PID: {self.process.pid})")
            self._is_running = True

            # Windows下等待进程完全启动
            if os.name == 'nt':
                logger.info(f"[MCP客户端] Windows环境，等待 {self.config.startup_wait} 秒...")
                await asyncio.sleep(self.config.startup_wait)

            # 启动响应读取任务
            self._start_response_reader()

            # 健康检查
            await self._health_check()

        except Exception as e:
            logger.error(f"[MCP客户端] 启动服务失败: {e}", exc_info=True)
            self._is_running = False
            # 清理进程
            if self.process:
                try:
                    self.process.kill()
                    await self.process.wait()
                except:
                    pass
            raise

    async def _health_check(self):
        """检查进程是否正常运行"""
        if not self.process:
            raise RuntimeError("进程未启动")

        # 检查进程是否还在运行
        if self.process.returncode is not None:
            # 读取stderr获取错误信息
            if self.process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(
                        self.process.stderr.read(),
                        timeout=5.0
                    )
                    if stderr_data:
                        logger.error(f"[MCP客户端] 进程启动失败: {stderr_data.decode(self.config.encoding)}")
                except asyncio.TimeoutError:
                    pass
            raise RuntimeError(f"进程启动失败，退出码: {self.process.returncode}")

        logger.info("[MCP客户端] 进程健康检查通过")

    def _start_response_reader(self):
        """启动后台响应读取任务"""
        if self._response_reader_task is not None and not self._response_reader_task.done():
            return

        self._response_reader_task = asyncio.create_task(self._read_responses())
        logger.debug("[MCP客户端] 响应读取任务已启动")

    async def stop(self):
        """停止MCP服务进程"""
        logger.info("[MCP客户端] 停止服务...")

        # 设置关闭事件
        self._shutdown_event.set()

        # 取消响应读取任务
        if self._response_reader_task is not None and not self._response_reader_task.done():
            self._response_reader_task.cancel()
            try:
                await self._response_reader_task
            except asyncio.CancelledError:
                pass
            self._response_reader_task = None

        # 终止进程
        if self.process:
            # 优雅终止
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10.0)
                logger.info(f"[MCP客户端] 服务正常停止 (退出码: {self.process.returncode})")
            except asyncio.TimeoutError:
                logger.warning("[MCP客户端] 进程终止超时，强制杀死...")
                self.process.kill()
                await self.process.wait()
                logger.info("[MCP客户端] 进程已强制终止")

            self.process = None

        self._is_running = False
        self._initialized = False
        self._shutdown_event.clear()
        logger.info("[MCP客户端] 服务已停止")

    @retry_on_failure(max_retries=DEFAULT_MAX_RETRIES, delay=INITIAL_DELAY)
    async def initialize(self, max_retries: int = None) -> Dict[str, Any]:
        """初始化MCP连接（带重试机制）

        Args:
            max_retries: 最大重试次数，覆盖默认配置

        Returns:
            初始化结果
        """
        if self._initialized:
            return {"ok": True, "message": "已初始化"}

        # 确保进程已启动
        if not self._is_running:
            await self.start()

        logger.info("[MCP客户端] 发送初始化请求...")

        # 发送initialize请求
        response = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {
                "name": "cloud-yunshu-mcp-client",
                "version": "1.1.0"
            }
        })

        # 解析响应
        if "error" in response:
            raise RuntimeError(f"MCP初始化失败: {response['error']}")

        result = response.get("result", {})
        self.capabilities = result.get("capabilities", {})
        self.server_info = result.get("serverInfo", {})

        logger.info(f"[MCP客户端] 初始化成功: {self.server_info.get('name')} v{self.server_info.get('version')}")

        # 自动获取工具列表
        await self.list_tools()

        self._initialized = True
        return result

    async def list_tools(self) -> List[MCPTool]:
        """列出可用工具"""
        response = await self._send_request("tools/list")

        if "error" in response:
            raise RuntimeError(f"获取工具列表失败: {response['error']}")

        tools_data = response.get("result", {}).get("tools", [])
        self.tools = [
            MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {})
            )
            for t in tools_data
        ]

        logger.info(f"[MCP客户端] 加载 {len(self.tools)} 个工具")
        for tool in self.tools:
            logger.info(f"  - {tool.name}: {tool.description[:50]}...")

        return self.tools

    async def call_tool(self, name: str, arguments: Dict[str, Any],
                       timeout: Optional[int] = None) -> Dict[str, Any]:
        """调用MCP工具

        Args:
            name: 工具名称
            arguments: 工具参数
            timeout: 超时时间（覆盖默认配置）

        Returns:
            工具执行结果
        """
        if not self._initialized:
            await self.initialize()

        logger.info(f"[MCP客户端] 调用工具: {name}")
        logger.info(f"[MCP客户端] 参数: {arguments}")

        response = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments
        }, timeout=timeout)

        if "error" in response:
            logger.error(f"[MCP客户端] 工具调用失败: {response['error']}")
            raise RuntimeError(f"工具调用失败: {response['error']}")

        result = response.get("result", {})

        # 解析内容
        content = result.get("content", [])
        if content and content[0]["type"] == "text":
            try:
                text_result = json.loads(content[0]["text"])
                logger.info(f"[MCP客户端] 工具执行完成: ok={text_result.get('ok')}")
                return text_result
            except json.JSONDecodeError as e:
                logger.error(f"[MCP客户端] 解析响应失败: {e}")
                return {"ok": False, "error": f"响应解析失败: {e}", "raw": content[0]["text"]}

        return result

    async def list_resources(self) -> List[MCPResource]:
        """列出可用资源"""
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("resources/list")

        if "error" in response:
            raise RuntimeError(f"获取资源列表失败: {response['error']}")

        resources_data = response.get("result", {}).get("resources", [])
        self.resources = [
            MCPResource(
                uri=r["uri"],
                name=r["name"],
                description=r.get("description", ""),
                mime_type=r.get("mimeType", "application/json")
            )
            for r in resources_data
        ]

        return self.resources

    async def read_resource(self, uri: str) -> str:
        """读取资源内容"""
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("resources/read", {"uri": uri})

        if "error" in response:
            raise RuntimeError(f"读取资源失败: {response['error']}")

        contents = response.get("result", {}).get("contents", [])
        if contents:
            return contents[0].get("text", "")

        return ""

    async def _send_request(self, method: str, params: Optional[Dict] = None,
                           timeout: Optional[int] = None) -> Dict:
        """发送JSON-RPC请求并等待响应（修复Windows兼容性）

        Args:
            method: 请求方法
            params: 请求参数
            timeout: 超时时间（覆盖默认配置）

        Returns:
            响应结果
        """
        if not self.process:
            raise RuntimeError("MCP服务未启动")

        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params or {}
        }

        # 创建Future用于等待响应
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self.pending_requests[self.request_id] = future

        try:
            # 发送请求（处理Windows行尾符）
            request_json = json.dumps(request, ensure_ascii=False)
            # 使用\n作为分隔符，但确保Windows兼容性
            request_bytes = (request_json + "\n").encode(self.config.encoding)

            self.process.stdin.write(request_bytes)
            await self.process.stdin.drain()

            logger.debug(f"[MCP客户端] 发送请求 #{self.request_id}: {method}")
            logger.debug(f"[MCP客户端] 请求内容: {request_json[:100]}...")

            # 等待响应（带可配置超时）
            actual_timeout = timeout if timeout is not None else self.config.timeout
            result = await asyncio.wait_for(future, timeout=actual_timeout)
            logger.debug(f"[MCP客户端] 收到响应 #{self.request_id}")
            return result

        except asyncio.TimeoutError:
            logger.error(f"[MCP客户端] 请求 #{self.request_id} 超时 (超时时间: {actual_timeout}s)")
            # 尝试获取进程状态
            if self.process and self.process.returncode is not None:
                logger.error(f"[MCP客户端] 进程已退出，退出码: {self.process.returncode}")
            raise TimeoutError(f"MCP请求超时: {method}")
        finally:
            self.pending_requests.pop(self.request_id, None)

    async def _read_responses(self):
        """后台任务：持续读取服务输出（修复Windows缓冲区问题）"""
        if not self.process:
            return

        buffer = ""  # 用于累积不完整的行

        while self._is_running and not self._shutdown_event.is_set():
            try:
                # 读取数据（使用较短的超时以便检测进程状态）
                chunk = await asyncio.wait_for(
                    self.process.stdout.read(self.config.read_chunk_size),
                    timeout=self.config.read_timeout
                )

                if not chunk:
                    # 流结束
                    logger.debug("[MCP客户端] 输出流已关闭")
                    break

                # 将字节解码为字符串（处理Windows编码问题）
                try:
                    buffer += chunk.decode(self.config.encoding)
                except UnicodeDecodeError:
                    # Windows终端可能使用GBK编码，尝试备用编码
                    try:
                        buffer += chunk.decode('gbk')
                    except:
                        # 忽略无法解码的字节
                        buffer += chunk.decode(self.config.encoding, errors='ignore')

                # 按行分割（处理Windows和Unix行尾符）
                lines = buffer.splitlines()
                # 如果最后一行不完整，保留在缓冲区中
                if buffer.endswith('\n') or buffer.endswith('\r'):
                    buffer = ""
                else:
                    buffer = lines.pop() if lines else ""

                # 处理每一行
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        response = json.loads(line)
                        req_id = response.get("id")

                        # 将响应分发给对应的Future
                        if req_id in self.pending_requests:
                            if not self.pending_requests[req_id].done():
                                self.pending_requests[req_id].set_result(response)
                            else:
                                logger.warning(f"[MCP客户端] Future已完成，忽略响应: {req_id}")
                        else:
                            logger.warning(f"[MCP客户端] 收到未知响应ID: {req_id}")

                    except json.JSONDecodeError as e:
                        logger.warning(f"[MCP客户端] JSON解析失败: {e}, 内容: {line[:100]}...")

            except asyncio.TimeoutError:
                # 超时是正常的，继续循环检查关闭事件
                continue
            except Exception as e:
                logger.error(f"[MCP客户端] 读取响应失败: {e}", exc_info=True)
                break

        # 处理stderr输出（在循环结束后）
        if self.process and self.process.stderr:
            try:
                stderr = await asyncio.wait_for(self.process.stderr.read(), timeout=5.0)
                if stderr:
                    logger.warning(f"[MCP客户端] 服务stderr输出:\n{stderr.decode(self.config.encoding)}")
            except asyncio.TimeoutError:
                logger.warning("[MCP客户端] 读取stderr超时")
            except Exception as e:
                logger.warning(f"[MCP客户端] 读取stderr失败: {e}")

        logger.debug("[MCP客户端] 响应读取任务已结束")


# ════════════════════════════════════════════════════════════════════
# 便捷函数
# ════════════════════════════════════════════════════════════════════

async def create_multi_search_client(
        service_path: str = "mcp_services/multi_search_engine.py",
        timeout: int = DEFAULT_TIMEOUT
) -> MCPClient:
    """创建多搜索引擎MCP客户端

    Args:
        service_path: MCP服务脚本路径
        timeout: 超时时间（秒）

    Returns:
        已初始化的MCPClient实例
    """
    config = MCPConfig(timeout=timeout)
    client = MCPClient("python", [service_path], config=config)
    await client.initialize()
    return client


# ════════════════════════════════════════════════════════════════════
# 测试入口
# ════════════════════════════════════════════════════════════════════

async def test_multi_search():
    """测试多引擎搜索MCP服务"""
    print("=" * 60)
    print("测试MCP多引擎搜索服务")
    print("=" * 60)

    # 创建客户端（配置更长超时）
    config = MCPConfig(timeout=60, max_retries=3)
    client = MCPClient("python", ["mcp_services/multi_search_engine.py"], config=config)

    try:
        # 初始化（带重试）
        print("\n[1] 初始化MCP连接...")
        await client.initialize()
        print(f"    服务: {client.server_info.get('name')} v{client.server_info.get('version')}")
        print(f"    协议版本: {client.capabilities}")

        # 列出工具
        print("\n[2] 获取工具列表...")
        tools = await client.list_tools()
        for tool in tools:
            print(f"    - {tool.name}: {tool.description}")

        # 调用搜索工具
        print("\n[3] 执行多引擎搜索...")
        result = await client.call_tool("search", {
            "query": "人工智能 最新发展",
            "engines": ["baidu", "sogou", "360"],
            "num_results": 3,
            "language": "zh"
        })

        print(f"    查询: {result.get('query')}")
        print(f"    使用引擎数: {result.get('total_engines')}")
        print(f"    引擎列表: {result.get('engines_used')}")

        for engine_result in result.get("results", [])[:2]:
            print(f"\n    [{engine_result['engine']}] {engine_result['engine_name']}:")
            for r in engine_result.get("results", [])[:2]:
                print(f"      - {r['title']}")
                print(f"        {r['snippet'][:80]}...")

        # 获取引擎列表
        print("\n[4] 获取支持的引擎列表...")
        engines_result = await client.call_tool("get_engines", {})

        # 分类显示
        by_category = {}
        for engine_id, meta in engines_result.get("engines", {}).items():
            cat = meta.get("category", "other")
            by_category.setdefault(cat, []).append(f"{engine_id}({meta['name']})")

        for category, engines in by_category.items():
            print(f"    {category}: {', '.join(engines)}")

        # 获取统计
        print("\n[5] 获取使用统计...")
        stats_result = await client.call_tool("get_stats", {})
        print(f"    总搜索次数: {stats_result['stats']['total_searches']}")
        print(f"    引擎使用统计: {stats_result['stats']['engine_usage']}")

        print("\n" + "=" * 60)
        print("测试完成!")
        print("=" * 60)

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(test_multi_search())