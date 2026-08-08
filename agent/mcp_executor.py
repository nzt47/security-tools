"""MCP 协议工具执行器 — 模拟远程工具调用

【不易】MCP 工具通过 protocol_endpoint 远程调用,不走本地 Python 函数;
        native 工具仍由现有 tool_router 路由,本模块只处理 protocol=mcp
【变易】模拟 MCP 协议流程(initialize → tools/list → tools/call),
        不依赖真实 MCP server,用 mock 响应演示完整调用链路
【简易】单文件实现,无第三方依赖;真实接入时替换 _mock_call 即可

协议流程(JSON-RPC 风格):
  1. initialize    — 客户端握手,获取 server 能力
  2. tools/list    — 查询 server 提供的工具列表
  3. tools/call    — 调用具体工具,传参数,获结果

真实接入时: 将 _mock_call 替换为 httpx.post(endpoint, json=request) 即可。
"""

import os
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Dict

# ════════════════════════════════════════════════════════════════
#  动态日志级别 — 生产环境临时排查用
# ════════════════════════════════════════════════════════════════
# 【不易】不改变现有 logger.info/debug 调用语义,仅控制级别过滤
# 【变易】双通道调整: .env 的 MCP_LOG_LEVEL(启动时) + set_logger_level(运行时)
# 【简易】单函数 set_logger_level,30s 可读;无效输入回退 INFO 不崩溃
#
# 使用方式:
#   1. .env 设 MCP_LOG_LEVEL=DEBUG,重启生效(适合计划内排查)
#   2. 运行时: from agent.mcp_executor import set_logger_level
#              set_logger_level("DEBUG")  # 立即生效,无需重启
#              set_logger_level("INFO")   # 排查完恢复
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_MCP_LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
# 回退追踪 — 供 healthcheck / Prometheus 指标 / 告警规则使用
_LOG_LEVEL_FALLBACK = False       # True 表示发生了无效值回退
_LOG_LEVEL_ORIGINAL: Optional[str] = None  # 原始无效值(回退时填充)
logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量 _executor 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None
# 模块加载时校验:CRITICAL 等非白名单值回退 INFO(防止抑制 ERROR 日志)
# CRITICAL 虽是 Python logging 内置级别(logging.CRITICAL=50),但会抑制
# ERROR(40)及以下日志,不属于运维白名单,视为恶意值/误配回退 INFO
if _MCP_LOG_LEVEL not in _VALID_LOG_LEVELS:
    logger.warning("[MCP] 无效日志级别 '%s',回退到 INFO。有效值: %s",
                   _MCP_LOG_LEVEL, sorted(_VALID_LOG_LEVELS))
    _LOG_LEVEL_ORIGINAL = _MCP_LOG_LEVEL
    _LOG_LEVEL_FALLBACK = True
    _MCP_LOG_LEVEL = "INFO"
logger.setLevel(getattr(logging, _MCP_LOG_LEVEL, logging.INFO))


def set_logger_level(level: str) -> str:
    """运行时动态调整 mcp_executor 日志级别(生产排查用)

    Args:
        level: "DEBUG" / "INFO" / "WARNING" / "ERROR" (大小写不敏感)

    Returns:
        实际生效的级别字符串(无效输入回退 INFO)

    Example:
        >>> set_logger_level("DEBUG")  # 开启进入/退出 + debug 日志
        'DEBUG'
        >>> set_logger_level("INFO")   # 排查完毕恢复默认
        'INFO'
    """
    level_upper = level.upper()
    if level_upper not in _VALID_LOG_LEVELS:
        logger.warning("[MCP] 无效日志级别 '%s',回退到 INFO。有效值: %s",
                       level, sorted(_VALID_LOG_LEVELS))
        level_upper = "INFO"
    logger.setLevel(getattr(logging, level_upper))
    logger.info("[MCP] 日志级别已调整为 %s", level_upper)
    return level_upper


def get_logger_level() -> str:
    """查询当前 mcp_executor 日志级别(便于运维确认)"""
    return logging.getLevelName(logger.getEffectiveLevel())


def get_log_level_status() -> dict:
    """查询日志级别完整状态 — 供 healthcheck / Prometheus 指标 / 告警规则使用。

    Returns:
        {
            "level": "INFO",           # 当前生效级别
            "configured": "CRITICAL",  # 环境变量原始配置值(upper)
            "fallback": True,          # 是否发生了无效值回退
            "original": "CRITICAL",    # 回退前的原始值(无回退时为 None)
            "valid_levels": ["DEBUG", "ERROR", "INFO", "WARNING"],
        }

    Example:
        >>> status = get_log_level_status()
        >>> if status["fallback"]:
        ...     print(f"告警: MCP_LOG_LEVEL={status['original']} 被回退到 INFO")
    """
    configured = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
    return {
        "level": get_logger_level(),
        "configured": configured,
        "fallback": _LOG_LEVEL_FALLBACK,
        "original": _LOG_LEVEL_ORIGINAL,
        "valid_levels": sorted(_VALID_LOG_LEVELS),
    }


# ════════════════════════════════════════════════════════════════
#  异常类型 — 区分超时/鉴权/其他,供 call_tool 精准捕获
# ════════════════════════════════════════════════════════════════

class McpAuthError(Exception):
    """MCP 鉴权失败(401/403) — endpoint 拒绝访问或 token 失效

    【变易】真实接入时,httpx 收到 401/403 应抛此异常,
    使 call_tool 返回明确的"鉴权失败"响应而非通用错误。
    """
    pass


class McpTimeoutError(TimeoutError):
    """MCP 调用超时 — 超过 timeout 未收到响应

    【变易】真实接入时,httpx.TimeoutException 应转为此类型,
    与原生 TimeoutError 区分以便日志精准定位 MCP 超时。
    """
    pass


# ════════════════════════════════════════════════════════════════
#  响应数据结构
# ════════════════════════════════════════════════════════════════

@dataclass
class McpResponse:
    """MCP 工具调用响应"""
    success: bool
    result: Any = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    tool_name: str = ""
    endpoint: str = ""
    protocol_phase: str = ""  # initialize / tools.list / tools.call

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "tool_name": self.tool_name,
            "endpoint": self.endpoint,
            "protocol_phase": self.protocol_phase,
        }


# ════════════════════════════════════════════════════════════════
#  MCP 客户端 — 模拟协议交互
# ════════════════════════════════════════════════════════════════

class McpClient:
    """模拟 MCP 客户端,走完整协议流程但不真实连接

    真实 MCP 使用 JSON-RPC over stdio/SSE;本类用 mock 响应模拟,
    保持协议语义(initialize → list → call)以便未来替换为真实实现。
    """

    def __init__(self, endpoint: str, timeout: float = 30.0):
        self.endpoint = endpoint
        self.timeout = timeout
        self.initialized = False
        self._server_info: Optional[dict] = None

    def initialize(self) -> McpResponse:
        """模拟 MCP initialize 握手

        真实协议: 发送 {method: "initialize", params: {protocolVersion, capabilities}}
        模拟: 校验 endpoint 格式,返回 server 信息
        """
        t0 = time.perf_counter()
        if not self.endpoint or not self.endpoint.startswith(("http://", "https://")):
            return McpResponse(
                success=False,
                error=f"endpoint 格式异常: {self.endpoint}",
                latency_ms=(time.perf_counter() - t0) * 1000,
                endpoint=self.endpoint,
                protocol_phase="initialize",
            )

        # 模拟网络延迟
        time.sleep(0.005)

        self._server_info = {
            "name": "mock-mcp-server",
            "version": "1.0.0",
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": True}},
        }
        self.initialized = True
        logger.debug("[MCP] initialize 成功: %s → %s", self.endpoint, self._server_info["name"])

        return McpResponse(
            success=True,
            result=self._server_info,
            latency_ms=(time.perf_counter() - t0) * 1000,
            endpoint=self.endpoint,
            protocol_phase="initialize",
        )

    def list_tools(self) -> McpResponse:
        """模拟 MCP tools/list — 返回远程工具列表

        真实协议: {method: "tools/list"}
        模拟: 返回空列表(工具 schema 由本地 YAML 定义,无需远程查询)
        """
        if not self.initialized:
            return McpResponse(
                success=False,
                error="MCP 未初始化,请先 initialize",
                protocol_phase="tools.list",
            )

        t0 = time.perf_counter()
        time.sleep(0.003)  # 模拟网络延迟
        # 工具定义在本地 YAML,远程 list 仅作协议完整性,返回空
        return McpResponse(
            success=True,
            result={"tools": []},
            latency_ms=(time.perf_counter() - t0) * 1000,
            endpoint=self.endpoint,
            protocol_phase="tools.list",
        )

    def call_tool(self, tool_name: str, arguments: dict) -> McpResponse:
        """模拟 MCP tools/call — 调用远程工具

        真实协议: {method: "tools/call", params: {name, arguments}}
        模拟: 根据 tool_name 返回 mock 结果

        日志策略: 进入/退出各记录一条 logger.info,含耗时与异常类型,
        便于后续排查(对齐用户排查需求)。异常分支统一 info 级别,
        通过消息中的"结果=xxx | 异常=xxx"字段过滤。
        """
        if not self.initialized:
            logger.info("[MCP] call_tool 拒绝 | tool=%s | endpoint=%s | 原因=未初始化",
                        tool_name, self.endpoint)
            return McpResponse(
                success=False,
                error="MCP 未初始化,请先 initialize",
                tool_name=tool_name,
                protocol_phase="tools.call",
            )

        t0 = time.perf_counter()
        # 进入日志:记录工具名与端点,便于关联同一次调用的进入/退出
        logger.info("[MCP] call_tool 进入 | tool=%s | endpoint=%s | timeout=%ss",
                    tool_name, self.endpoint, self.timeout)
        try:
            result = self._mock_call(tool_name, arguments)
            latency = (time.perf_counter() - t0) * 1000
            logger.info("[MCP] call_tool 退出 | tool=%s | 耗时=%.1fms | 结果=成功 | 异常=无",
                        tool_name, latency)
            return McpResponse(
                success=True,
                result=result,
                latency_ms=latency,
                tool_name=tool_name,
                endpoint=self.endpoint,
                protocol_phase="tools.call",
            )
        except McpAuthError as e:
            latency = (time.perf_counter() - t0) * 1000
            logger.info("[MCP] call_tool 退出 | tool=%s | 耗时=%.1fms | 结果=鉴权失败 | 异常=McpAuthError: %s",
                        tool_name, latency, e)
            return McpResponse(
                success=False,
                error=f"鉴权失败: {e}",
                latency_ms=latency,
                tool_name=tool_name,
                endpoint=self.endpoint,
                protocol_phase="tools.call",
            )
        except TimeoutError:
            latency = (time.perf_counter() - t0) * 1000
            logger.info("[MCP] call_tool 退出 | tool=%s | 耗时=%.1fms | 结果=超时 | 异常=TimeoutError",
                        tool_name, latency)
            return McpResponse(
                success=False,
                error=f"调用超时 ({self.timeout}s)",
                latency_ms=latency,
                tool_name=tool_name,
                endpoint=self.endpoint,
                protocol_phase="tools.call",
            )
        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000
            logger.info("[MCP] call_tool 退出 | tool=%s | 耗时=%.1fms | 结果=异常 | 异常=%s: %s",
                        tool_name, latency, type(e).__name__, e)
            return McpResponse(
                success=False,
                error=str(e),
                latency_ms=latency,
                tool_name=tool_name,
                endpoint=self.endpoint,
                protocol_phase="tools.call",
            )

    def _mock_call(self, tool_name: str, arguments: dict) -> Any:
        """mock 响应 — 根据 tool_name 返回模拟数据

        真实接入时替换此方法为:
            response = httpx.post(self.endpoint, json={
                "jsonrpc": "2.0", "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": 1,
            }, timeout=self.timeout)
            return response.json()["result"]
        """
        # 模拟远程处理延迟
        time.sleep(0.01)

        if tool_name == "db_query":
            sql = arguments.get("sql", "")
            db = arguments.get("database", "main")
            # 模拟 SQL 查询结果
            return {
                "rows": [
                    {"id": 1, "name": "张三", "email": "zhangsan@example.com"},
                    {"id": 2, "name": "李四", "email": "lisi@example.com"},
                ],
                "affected": 2,
                "database": db,
                "sql_executed": sql,
            }

        elif tool_name == "remote_file_read":
            path = arguments.get("path", "")
            return {
                "content": f"[模拟] 文件 {path} 的内容\n第一行数据\n第二行数据",
                "size": 48,
                "encoding": arguments.get("encoding", "utf-8"),
                "path": path,
            }

        elif tool_name == "api_webhook":
            url = arguments.get("url", "")
            method = arguments.get("method", "POST")
            return {
                "status": 200,
                "method": method,
                "url": url,
                "body": {"ok": True, "message": "webhook 已接收", "received_at": "2026-07-24T10:00:00Z"},
            }

        else:
            # 通用 mock:未识别的工具返回原参数
            return {"mock": True, "tool": tool_name, "args": arguments, "note": "未识别工具,返回通用 mock"}


# ════════════════════════════════════════════════════════════════
#  MCP 执行器 — 协调 YAML 定义 + McpClient 调用
# ════════════════════════════════════════════════════════════════

class McpExecutor:
    """MCP 工具执行器

    职责:
    1. 从 TOOL_PROTOCOL_MAP 查询工具的 protocol/endpoint
    2. 按 endpoint 复用 McpClient(连接池语义)
    3. 执行完整协议流程: initialize → tools/call
    4. 返回结构化 McpResponse
    """

    def __init__(self, default_timeout: float = 30.0):
        """初始化 MCP 执行器

        Args:
            default_timeout: 默认调用超时(秒),透传给 McpClient;
                             CLI/真实环境可通过此参数覆盖默认 30s
        """
        self._clients: Dict[str, McpClient] = {}  # endpoint → client
        self._default_timeout = default_timeout

    def execute(self, tool_name: str, arguments: dict,
                protocol_info: Optional[dict] = None) -> McpResponse:
        """执行 MCP 协议工具

        Args:
            tool_name: 工具名(需在 TOOL_PROTOCOL_MAP 中声明 protocol=mcp)
            arguments: 调用参数
            protocol_info: 可选,外部传入的 {protocol, endpoint};
                          None 时从 TOOL_PROTOCOL_MAP 查询

        Returns:
            McpResponse: 结构化响应
        """
        # 查询协议信息
        if protocol_info is None:
            protocol_info = self._lookup_protocol(tool_name)
            if protocol_info is None:
                return McpResponse(
                    success=False,
                    error=f"工具 {tool_name} 未在 TOOL_PROTOCOL_MAP 中注册",
                    tool_name=tool_name,
                )

        if protocol_info.get("protocol") != "mcp":
            return McpResponse(
                success=False,
                error=f"非 mcp 协议工具: {tool_name} (protocol={protocol_info.get('protocol')})",
                tool_name=tool_name,
            )

        endpoint = protocol_info.get("endpoint", "")
        if not endpoint:
            return McpResponse(
                success=False,
                error=f"mcp 工具 {tool_name} 未配置 protocol_endpoint",
                tool_name=tool_name,
            )

        # 获取/复用 client
        client = self._get_client(endpoint)

        # 协议流程: initialize(首次) → tools/call
        if not client.initialized:
            init_resp = client.initialize()
            if not init_resp.success:
                return init_resp

        # 执行调用
        return client.call_tool(tool_name, arguments)

    def _get_client(self, endpoint: str) -> McpClient:
        """按 endpoint 复用 client(模拟连接池),透传 default_timeout"""
        if endpoint not in self._clients:
            self._clients[endpoint] = McpClient(endpoint, timeout=self._default_timeout)
        return self._clients[endpoint]

    def _lookup_protocol(self, tool_name: str) -> Optional[dict]:
        """从 TOOL_PROTOCOL_MAP 查询工具协议信息(延迟导入避免循环)"""
        try:
            from agent.tool_router import TOOL_PROTOCOL_MAP
            return TOOL_PROTOCOL_MAP.get(tool_name)
        except ImportError:
            logger.warning("[MCP] 无法导入 TOOL_PROTOCOL_MAP,tool_router 不可用")
            return None


# ════════════════════════════════════════════════════════════════
#  模块级单例 + 便捷接口
# ════════════════════════════════════════════════════════════════

_executor: Optional[McpExecutor] = None  # 保留作为 fallback


def _create_mcp_executor(config=None):
    """McpExecutor 工厂（供 SingletonManager 使用）

    config 走 dict 通道（{"default_timeout": <float>}），
    仅当 dict 含该键才解包，否则用默认 30s。
    """
    if isinstance(config, dict) and "default_timeout" in config:
        return McpExecutor(default_timeout=config["default_timeout"])
    return McpExecutor()


def get_mcp_executor() -> McpExecutor:
    """获取 MCP 执行器单例

    Returns:
        McpExecutor 实例
    """
    if _SINGLETON_AVAILABLE:
        return get_singleton("mcp_executor")
    global _executor
    if _executor is None:
        _executor = _create_mcp_executor()
    return _executor


def reset_mcp_executor():
    """重置 MCP 执行器单例（仅用于测试）"""
    global _executor
    if _SINGLETON_AVAILABLE:
        reset_singleton("mcp_executor")
    _executor = None


def execute_mcp_tool(tool_name: str, arguments: dict) -> McpResponse:
    """便捷接口: 执行 MCP 协议工具

    自动从 TOOL_PROTOCOL_MAP 查询协议信息,执行完整调用流程。

    Example:
        >>> resp = execute_mcp_tool("db_query", {"sql": "SELECT 1"})
        >>> print(resp.success, resp.result)
    """
    return get_mcp_executor().execute(tool_name, arguments)


def is_mcp_tool(tool_name: str) -> bool:
    """判断工具是否为 mcp 协议"""
    try:
        from agent.tool_router import get_tool_protocol
        return get_tool_protocol(tool_name) == "mcp"
    except ImportError:
        return False


# ════════════════════════════════════════════════════════════════
#  CLI 入口 — --verbose 快速排查
# ════════════════════════════════════════════════════════════════
# 【不易】仅 __main__ 块新增,不改变模块级接口与导入语义
# 【变易】--verbose 启动时直接 set_logger_level("DEBUG"),无需改 .env 或重启
# 【简易】直接 python agent/mcp_executor.py -v 即可排查,30s 可读
#
# 用法:
#   python agent/mcp_executor.py              # 默认 INFO 级别自检
#   python agent/mcp_executor.py --verbose    # DEBUG 级别,输出详细协议日志
#   python agent/mcp_executor.py -v --tool db_query --endpoint https://...

# 注册单例工厂（置于 __main__ 块之前，确保 get_mcp_executor / 便捷函数均已定义）
# 无 cleanup 钩子：McpExecutor 持有的是内存模拟连接池（_mock_call），无真实资源
if _SINGLETON_AVAILABLE:
    register_singleton("mcp_executor", _create_mcp_executor)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="MCP 执行器快速排查工具 — 验证协议流程与日志输出",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python agent/mcp_executor.py --verbose              # DEBUG 级别自检
  python agent/mcp_executor.py -v --tool db_query     # 指定工具
  python agent/mcp_executor.py --endpoint https://...  # 指定端点
        """,
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="启用 DEBUG 级别日志(输出 initialize/list_tools 等详细协议日志)",
    )
    parser.add_argument(
        "--endpoint",
        default="https://mcp.example.com/test",
        help="MCP server 端点(默认: mock 端点,仅协议演示)",
    )
    parser.add_argument(
        "--tool",
        default="db_query",
        help="测试工具名(默认: db_query)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="调用超时秒数(默认: 5)",
    )
    args = parser.parse_args()

    # 配置 logging 输出到控制台(确保日志可见)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # --verbose: 启动时直接切换 DEBUG,输出详细协议日志
    if args.verbose:
        set_logger_level("DEBUG")
        print(f"[CLI] --verbose 模式: 日志级别已切换为 {get_logger_level()}")
    else:
        print(f"[CLI] 默认模式: 日志级别={get_logger_level()}")

    # ERROR 探针:验证 ERROR 级别日志可见(若级别被恶意设为 CRITICAL 且未修复则被抑制)
    # 正常情况下输出到 stderr,证明 ERROR 级别日志链路畅通
    logger.error("[MCP] 自检探针 | 级别=%s | endpoint=%s | tool=%s",
                 get_logger_level(), args.endpoint, args.tool)

    print(f"\n{'='*60}")
    print(f"  MCP 执行器自检  (endpoint={args.endpoint}, tool={args.tool})")
    print(f"{'='*60}")

    # 协议流程: initialize → call_tool
    client = McpClient(args.endpoint, timeout=args.timeout)

    print("\n[步骤 1] initialize 握手...")
    init_resp = client.initialize()
    print(f"  success={init_resp.success}, latency={init_resp.latency_ms:.1f}ms")
    if not init_resp.success:
        print(f"  error: {init_resp.error}")
        raise SystemExit(1)

    print(f"\n[步骤 2] call_tool({args.tool})...")
    resp = client.call_tool(args.tool, {"sql": "SELECT 1", "database": "main"})
    print(f"  success={resp.success}, latency={resp.latency_ms:.1f}ms")
    if resp.success:
        print(f"  result: {resp.result}")
    else:
        print(f"  error: {resp.error}")

    print(f"\n{'='*60}")
    print(f"  自检完成。当前日志级别: {get_logger_level()}")
    print(f"  运行时恢复 INFO: from agent.mcp_executor import set_logger_level; set_logger_level('INFO')")
    print(f"{'='*60}")
