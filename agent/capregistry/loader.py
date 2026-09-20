"""`Loader` —— `location` 的**唯一分叉点**（v1.4 §4 / §8.1）

## 缺口在哪（审计结论，本模块针对它而建）

`TASK-05` §2.2 的实测：仓库**没有**统一的 Loader 抽象，`agent/loader.py` /
`capability_loader.py` / `lines/loader.py` 均不存在。MCP 侧三条客户端链路
**两条是假的**（`yunshu_mcp_bridge.py` 纯 mock、`mcp_executor.py` mock + 零调用方），
官方 `mcp` SDK **未安装**，MCP 传输层**无熔断**（9 个 MCP 文件搜
`circuit|breaker` 零命中）。而仓库里通用熔断器、`Sandbox`、`HttpClient`
**都已存在** ⇒ **缺口在"接线"而非"建设"**。

## 四个实现（v1.4 §8.1 加载策略矩阵）与**真实状态**

| 类型 | 策略 | 本模块的实现状态 |
|---|---|---|
| Local（TL） | 启动扫描常驻 | **真实现**：包装 `agent/tools/__init__.py::_registry` 的既有链路，**不重写** |
| Stdio（TR） | session 随会话启停 + 预热池 | **真实现并已端到端验证**：驱动仓库自带的 `mcp_services/yunshu_mcp_server.py`（它是**手写 JSON-RPC over stdio**，**不需要官方 `mcp` SDK**）⇒ 无 mock |
| SSE（TR） | eager + 连接池 | **真实现，但无生产端点**：协议按 MCP SSE 传输实现（`GET /sse` → `event: endpoint` → `POST`），验证用的是**本地真实 HTTP 服务**（`tests/unit/test_capregistry_loader.py` 里起 `http.server`），**不是** mock 掉本模块代码 |
| HTTP（TR） | eager + 连接池 | **同上**：MCP streamable-HTTP（单 POST + JSON/SSE 响应）。**必须**经 `agent/web/http_client.py::HttpClient`（否则绕过 EgressGuard —— 见 TASK-07） |

> ⚠️ **诚实标注**：SSE / HTTP 两个 Loader 的代码路径是**真实可用**的，但
> **仓库里没有任何生产 SSE/HTTP MCP 端点**（`data/mcp_services.json` 无此类登记）。
> 因此它们的"生产可用性"**未经验证**，只有"对真实 HTTP 服务可用"这一条实测结论。
> 不得把它们表述为"生产链路已就绪"。

## 熔断与退避（§3 第 2 步第 3 项）

复用 `agent/circuit_breaker.py`（已存在且广泛使用）。`TASK-00` 已确认
"工具循环有熔断、MCP 传输层没有" ⇒ 本模块就是**把熔断接到传输层**：
每次 `invoke()` 先过 `breaker.allow_request()`，成功/失败分别回报。

状态机（v1.4 §8.1）：`idle → connecting → ready → unhealthy → backoff → connecting`。
`backoff` 的等待时长按指数增长并封顶；`now` 可注入（**墙钟计时的断言不靠 sleep**）。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "Handle",
    "InvokeOutcome",
    "Loader",
    "LoaderManager",
    "LoaderState",
    "LocalLoader",
    "HttpLoader",
    "SseLoader",
    "StdioLoader",
    "LoaderInitError",
    "LoaderUnavailable",
    "get_loader_manager",
    "reset_loader_manager",
]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class LoaderInitError(RuntimeError):
    """Loader 初始化失败（**必须被逐 Loader 捕获**，不得冒泡到启动链路，见 D4）"""


class LoaderUnavailable(RuntimeError):
    """该 Loader 当前不可用（未连接 / 熔断打开 / 退避中）"""


class LoaderState(str, Enum):
    """连接状态机（v1.4 §8.1）"""

    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    BACKOFF = "backoff"


@dataclass(frozen=True)
class Handle:
    """`resolve()` 的产物：一个**可调用句柄**

    【为什么单独一层】`load_tool_meta` 是元数据，`_registry[name]["handler"]` 是
    进程内对象，远端则是"某个端点上的某个工具名"。三种东西形状完全不同，
    `Handle` 是它们的**共同最小面**：调用方只认 `capability` + `loader`。
    """

    capability: str
    loader: str
    location: str
    endpoint: str = ""
    version: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability, "loader": self.loader,
            "location": self.location, "endpoint": self.endpoint,
            "version": self.version, "extra": dict(self.extra),
        }


@dataclass
class InvokeOutcome:
    """一次调用的结果（`invoke.py` 据此构造统一信封）"""

    ok: bool
    data: Any = None
    code: str = "ok"
    detail: str = ""
    duration_ms: float = 0.0
    loader: str = ""
    path: str = ""
    #: 原始异常（**仅供 `errors.from_exception()` 做精确归码**，不对外序列化）
    #: 【为什么必须带上】Loader 只知道"传输层失败了"，而"未知工具"（`not_found`）
    #: 与"工具执行炸了"（`internal_error`）对调用方是**完全不同的处置**。
    #: 只传 `code="unhealthy"` 会把两者抹平 —— 那正是本仓库"错误码不可用"的老毛病。
    exception: Optional[BaseException] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "code": self.code, "loader": self.loader,
                "path": self.path, "duration_ms": self.duration_ms}


# ════════════════════════════════════════════════════════════
#  基类：状态机 + 熔断 + 退避
# ════════════════════════════════════════════════════════════


class Loader:
    """Loader 基类（子类只需实现 `_do_connect` / `_do_invoke`）"""

    #: 子类覆盖：`local` / `stdio` / `sse` / `http`
    kind: str = ""
    #: 该 Loader 负责的 `location`（`local` 或 `remote`）
    location: str = "remote"

    #: 指数退避参数（秒）
    BACKOFF_BASE = 0.5
    BACKOFF_MAX = 30.0

    def __init__(self, name: str = "", *,
                 now: Optional[Callable[[], float]] = None,
                 breaker: Any = None) -> None:
        self.name = name or self.kind
        #: 注入式时钟（**墙钟断言一律注入时间**，见 TASK-05 运行纪律第 8 条）
        self._now = now or time.monotonic
        self._state = LoaderState.IDLE
        self._lock = threading.RLock()
        self._failures = 0
        self._next_retry_at = 0.0
        self._last_error = ""
        self._handle: Optional[Handle] = None
        self._breaker = breaker
        #: 结构化告警（**不抛异常**；由 `LoaderManager` 汇总后进入 `/capabilities/health`）
        self.warnings: List[str] = []

    # ── 熔断器（惰性构造；不可用时降级为"无熔断"，绝不让 Loader 起不来）──

    def _get_breaker(self) -> Any:
        if self._breaker is not None:
            return self._breaker
        if self.kind == "local":
            # 【为什么 Local 不挂熔断】本进程内函数调用没有"连接"可言，失败是
            # 业务失败而非传输失败；给它挂熔断会把"某个工具报错 3 次"升级成
            # "整个本地能力面被切断" —— 爆炸半径与收益不成比例。
            return None
        try:
            from agent.circuit_breaker import (  # noqa: PLC0415 惰性：可选依赖
                CircuitBreaker, CircuitBreakerConfig)
            self._breaker = CircuitBreaker(CircuitBreakerConfig(
                name=f"capregistry.{self.kind}",
                failure_threshold=0.5, min_requests=3,
                reset_timeout=30.0, window_seconds=60.0, max_attempts=2))
        except Exception as exc:  # noqa: BLE001  熔断器不可用 ⇒ 无熔断（比起不来好）
            self.warnings.append(f"熔断器不可用（{self.kind} 无熔断保护）: {exc}")
            self._breaker = None
        return self._breaker

    # ── 状态机 ──

    @property
    def state(self) -> LoaderState:
        """当前状态；`backoff` 到期后**自动**转 `connecting`（v1.4 §8.1 的闭环）"""
        with self._lock:
            if self._state == LoaderState.BACKOFF and self._now() >= self._next_retry_at:
                self._state = LoaderState.CONNECTING
            return self._state

    def _set_state(self, st: LoaderState, *, error: str = "") -> None:
        with self._lock:
            self._state = st
            if error:
                self._last_error = str(error)
                self.warnings.append(f"[{self.kind}] {error}")
                # 告警列表有界（避免长期运行下无界增长）
                if len(self.warnings) > 50:
                    del self.warnings[:-50]

    @property
    def last_error(self) -> str:
        """最近一次失败原因（**公开只读**：调用方不该去碰 `_last_error`）"""
        return self._last_error

    @property
    def ready(self) -> bool:
        """是否已就绪（公开只读，替代对 `_handle` 的私有访问）"""
        with self._lock:
            return self._state == LoaderState.READY and self._handle is not None

    def _enter_backoff(self, error: str) -> None:
        with self._lock:
            self._failures += 1
            delay = min(self.BACKOFF_BASE * (2 ** (self._failures - 1)), self.BACKOFF_MAX)
            self._next_retry_at = self._now() + delay
        self._set_state(LoaderState.BACKOFF, error=error)
        logger.warning("[capregistry.loader:%s] 进入退避 %.1fs（第 %d 次失败）: %s",
                       self.kind, delay, self._failures, error)

    # ── 对外三步：resolve / connect / invoke ──

    def supports(self, spec: Any) -> Tuple[bool, str]:
        """该 Loader 是否能承载这条能力（子类覆盖；基类按 `location` 判定）"""
        if str(getattr(spec, "location", "")) != self.location:
            return False, f"location={getattr(spec, 'location', '?')} 不属于 {self.kind}"
        return True, ""

    def resolve(self, spec: Any) -> Handle:
        """元数据 → 句柄（**纯函数式**：不发网络、不 spawn 进程）"""
        return Handle(capability=str(getattr(spec, "tool_name", "")),
                      loader=self.kind, location=self.location,
                      version=str(getattr(spec, "version", "") or ""))

    def connect(self, handle: Handle) -> bool:
        """建立连接（或 import）。**失败只置状态与告警，不抛**（D4）"""
        with self._lock:
            if self._state == LoaderState.BACKOFF and self._now() < self._next_retry_at:
                return False
            self._state = LoaderState.CONNECTING
        try:
            self._do_connect(handle)
        except Exception as exc:  # noqa: BLE001  任何连接失败都必须被吞掉
            self._handle = None
            self._enter_backoff(f"连接失败: {type(exc).__name__}: {exc}")
            return False
        with self._lock:
            self._handle = handle
            self._failures = 0
            self._state = LoaderState.READY
            self._last_error = ""
        self._on_ready(handle)
        return True

    def invoke(self, handle: Handle, args: Mapping[str, Any]) -> InvokeOutcome:
        """调用。熔断打开 / 退避中 ⇒ 直接返回 `unhealthy`，不发起真实调用"""
        start = self._now()
        breaker = self._get_breaker()
        if breaker is not None:
            try:
                if not breaker.allow_request():
                    return InvokeOutcome(
                        ok=False, code="unhealthy", loader=self.kind, path=self.kind,
                        detail="熔断器处于打开状态（连续失败），已直接拒绝",
                        duration_ms=(self._now() - start) * 1000.0)
            except Exception:  # noqa: BLE001  熔断器故障 ⇒ 放行（fail-open）
                pass
        if self.state in (LoaderState.BACKOFF, LoaderState.UNHEALTHY):
            return InvokeOutcome(
                ok=False, code="unhealthy", loader=self.kind, path=self.kind,
                detail=f"{self.kind} 当前不可用（state={self.state.value}）",
                duration_ms=(self._now() - start) * 1000.0)
        try:
            data = self._do_invoke(handle, args)
        except Exception as exc:  # noqa: BLE001  统一收口成 InvokeOutcome
            if breaker is not None:
                try:
                    breaker.record_result(False)
                except Exception:  # noqa: BLE001
                    pass
            self._enter_backoff(f"调用失败: {type(exc).__name__}: {exc}")
            return InvokeOutcome(
                ok=False, code="unhealthy", loader=self.kind, path=self.kind,
                detail=str(exc), exception=exc,
                duration_ms=(self._now() - start) * 1000.0)
        if breaker is not None:
            try:
                breaker.record_result(True)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._failures = 0
        return InvokeOutcome(ok=True, data=data, loader=self.kind, path=self.kind,
                             duration_ms=(self._now() - start) * 1000.0)

    def health(self) -> str:
        """健康态字符串（进入 `CapabilityRecord.health`；语义见 `view.is_healthy`）"""
        st = self.state
        if st == LoaderState.READY:
            return "healthy"
        if st in (LoaderState.BACKOFF, LoaderState.UNHEALTHY):
            return "unhealthy"
        if st == LoaderState.CONNECTING:
            return "connecting"
        return "unknown"

    def describe(self) -> Dict[str, Any]:
        return {"loader": self.kind, "location": self.location, "state": self.state.value,
                "health": self.health(), "failures": self._failures,
                "last_error": self._last_error, "warnings": list(self.warnings[-5:])}

    def close(self) -> None:
        """释放资源（子类覆盖；基类只重置句柄）"""
        with self._lock:
            self._handle = None
            self._state = LoaderState.IDLE

    def eager_init(self) -> None:
        """初始化探测（**启动期调用一次**；基类默认无操作）

        【不易】基类**不**做任何事：本地 Loader 没有"连接"可言，
        加一次 `connect()` 只是无意义的开销。需要探测的子类自行覆盖。
        【D4】本方法**允许抛**（抛出即"初始化失败"），但**必须**由
        `LoaderManager._build()` 逐 Loader 捕获 —— 异常绝不冒泡到启动链路。
        """
        return None

    # ── 子类钩子 ──

    def _do_connect(self, handle: Handle) -> None:  # pragma: no cover - 基类无连接
        return None

    def _on_ready(self, handle: Handle) -> None:  # pragma: no cover - 可选钩子
        return None

    def _do_invoke(self, handle: Handle, args: Mapping[str, Any]) -> Any:
        raise LoaderUnavailable(f"{self.kind} Loader 未实现 _do_invoke")


# ════════════════════════════════════════════════════════════
#  ① Local（TL）：包装既有注册表，**不重写**
# ════════════════════════════════════════════════════════════


class LocalLoader(Loader):
    """`location=local` ⇒ 进程内调用，**必须**经 `agent/tools/__init__.py::call()`

    【为什么只是包装】`call()` 是工具分发的唯一汇聚点（闸门/限流/审计/健康都在
    它里面）。任何"为了架构好看而绕过它"的实现都会把治理面撕开一个洞 ——
    这正是 `TASK-05` §2.3b 记录的历史教训。
    """

    kind = "local"
    location = "local"

    def _do_connect(self, handle: Handle) -> None:
        # 本地不需要连接；但**必须**验证注册框架可导入，否则"连接失败"才是真事实
        from agent import tools as _tools  # noqa: PLC0415 惰性：避免 import 环
        if not hasattr(_tools, "call"):
            raise LoaderInitError("agent.tools 缺少 call()（注册框架不完整）")

    def _do_invoke(self, handle: Handle, args: Mapping[str, Any]) -> Any:
        from agent import tools as _tools  # noqa: PLC0415
        return _tools.call(handle.capability, **dict(args))

    def eager_init(self) -> None:
        """启动期探测：注册框架**可导入且有 `call()`**（本地链路的前置）

        只做 import 与属性检查，**不触碰任何工具**（不产生副作用、不占线程）
        —— 本方法的成本必须与"启动期"相称。
        """
        self._do_connect(Handle(capability="<probe>", loader=self.kind,
                                location=self.location))
        self._set_state(LoaderState.READY)


# ════════════════════════════════════════════════════════════
#  ② Stdio（TR）：真实子进程 + 行分隔 JSON-RPC
# ════════════════════════════════════════════════════════════


class _JsonRpcProcess:
    """行分隔 JSON-RPC 2.0 子进程会话（读线程 + 队列，避免无超时阻塞）

    【为什么必须有读线程】直接用 `proc.stdout.readline()` 是**无超时**的：
    服务端不响应就把调用线程永久挂住。waitress 只有 16 个线程，
    挂住 16 个就等于整个平台失去响应。故用读线程 + `queue.get(timeout=...)`。
    """

    def __init__(self, argv: Sequence[str], *, cwd: str = "",
                 env: Optional[Mapping[str, str]] = None,
                 timeout: float = 20.0) -> None:
        self.timeout = float(timeout)
        self._id = 0
        self._lock = threading.Lock()
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._closed = False
        # stderr 送 DEVNULL：stdio 的 stdout 是**协议流**，日志混进来即报废
        # （yunshu_mcp_server 自己已经把日志固定到 stderr，这里再兜一层）
        self._proc = subprocess.Popen(  # noqa: S603  argv 完全由本仓库常量构造
            list(argv), cwd=cwd or _REPO_ROOT, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=dict(env) if env is not None else None)
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name=f"capregistry-stdio-{os.getpid()}")
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._queue.put(json.loads(line))
                except json.JSONDecodeError:
                    # 非 JSON 行一律丢弃而不是崩：协议流被污染时也要能活到超时
                    logger.debug("[capregistry.stdio] 丢弃非 JSON 行: %.120s", line)
        except Exception:  # noqa: BLE001  进程退出 / 管道关闭
            pass

    def request(self, method: str, params: Optional[dict] = None) -> dict:
        with self._lock:
            self._id += 1
            req_id = self._id
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method,
                       "params": dict(params or {})}
            if self._proc.stdin is None:
                raise LoaderUnavailable("子进程 stdin 不可用")
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"stdio 响应超时（{self.timeout}s, method={method}）")
            try:
                msg = self._queue.get(timeout=min(remain, 0.5))
            except queue.Empty:
                if self._proc.poll() is not None:
                    raise LoaderUnavailable(
                        f"子进程已退出（returncode={self._proc.returncode}）")
                continue
            if msg.get("id") == req_id:
                return msg
            # 不是我们这一帧（服务端通知 / 迟到响应）：放回并继续等
            self._queue.put(msg)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass


class StdioLoader(Loader):
    """`location=remote` 且传输为 stdio ⇒ **真实子进程**（本机 stdio MCP 也算 remote）

    【判定铁律】跨进程/协议边界即 remote（`TASK-00` §0.4）—— 本机 stdio 也是 remote。

    【真实链路，不是 mock】驱动的是仓库自带的 `mcp_services/yunshu_mcp_server.py`。
    该服务端是**手写 JSON-RPC 2.0 over stdio**（不依赖官方 `mcp` SDK），
    故官方 SDK 未安装**不影响**本 Loader。
    """

    kind = "stdio"
    location = "remote"

    #: 默认服务端脚本（`CP_CAPABILITY_STDIO_SERVER` 可覆写，便于 CI 指向桩服务）
    DEFAULT_SERVER = os.path.join("mcp_services", "yunshu_mcp_server.py")

    def __init__(self, name: str = "", *, server: str = "",
                 prewarm: int = 0, timeout: float = 20.0, **kw: Any) -> None:
        super().__init__(name or "stdio", **kw)
        self.server = server or self.DEFAULT_SERVER
        self.prewarm = max(0, int(prewarm))
        self.timeout = float(timeout)
        #: 连接的会话（**预热池**：`prewarm` 个常驻会话 + 按需补足）
        self._pool: List[_JsonRpcProcess] = []
        self._pool_lock = threading.RLock()

    # 状态机复用基类的"backoff 期间不重连"，但预热池要独立于单句柄
    def _spawn(self) -> _JsonRpcProcess:
        script = self.server
        if not os.path.isabs(script):
            script = os.path.join(_REPO_ROOT, script)
        if not os.path.isfile(script):
            raise LoaderInitError(f"stdio 服务端脚本不存在: {script}")
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = _JsonRpcProcess([sys.executable, script], cwd=_REPO_ROOT,
                               env=env, timeout=self.timeout)
        resp = proc.request("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "capregistry-loader", "version": "1.0.0"},
            "capabilities": {},
        })
        if "error" in resp:
            proc.close()
            raise LoaderInitError(f"initialize 失败: {resp['error']}")
        return proc

    def _do_connect(self, handle: Handle) -> None:
        with self._pool_lock:
            while len(self._pool) < max(1, self.prewarm):
                self._pool.append(self._spawn())
            if not self._pool:
                self._pool.append(self._spawn())

    def _do_invoke(self, handle: Handle, args: Mapping[str, Any]) -> Any:
        with self._pool_lock:
            if not self._pool:
                self._pool.append(self._spawn())
            proc = self._pool[0]
        resp = proc.request("tools/call", {"name": handle.capability,
                                           "arguments": dict(args)})
        if "error" in resp:
            raise LoaderUnavailable(f"JSON-RPC 错误: {resp['error']}")
        result = resp.get("result") or {}
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        raw = texts[0] if texts else ""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw, "isError": bool(result.get("isError"))}

    def pool_size(self) -> int:
        with self._pool_lock:
            return len(self._pool)

    def eager_init(self) -> None:
        """启动期探测：**校验服务端脚本存在**，`prewarm>0` 时顺带把池子预热起来

        【为什么启动期必须校验脚本存在】不校验的话，"配置指错了 stdio 服务端"
        要等到**第一次业务调用**才失败 —— 那时调用方看到的是超时，
        而不是"配置错了"。启动期把配置错误暴露出来是 O(1) 的成本。
        【为什么 prewarm=0 时**不**真的 spawn】起一个常驻子进程有真实成本
        （内存 + 句柄），默认不预热（v1.4 §8.1 的 stdio 预热是**目标**，
        是否常开应由数据侧 `CP_CAPABILITY_STDIO_PREWARM` 决定）。
        """
        script = self.server
        if not os.path.isabs(script):
            script = os.path.join(_REPO_ROOT, script)
        if not os.path.isfile(script):
            self._set_state(LoaderState.UNHEALTHY,
                            error=f"stdio 服务端脚本不存在: {script}")
            raise LoaderInitError(f"stdio 服务端脚本不存在: {script}")
        if self.prewarm > 0:
            if not self.connect(Handle(capability="<probe>", loader=self.kind,
                                       location=self.location)):
                raise LoaderInitError(
                    f"stdio 预热池建立失败: {self.last_error or '未知原因'}")
        else:
            self._set_state(LoaderState.IDLE)
            self._handle = Handle(capability="<probe>", loader=self.kind,
                                  location=self.location)

    def close(self) -> None:
        with self._pool_lock:
            procs, self._pool = self._pool, []
        for p in procs:
            try:
                p.close()
            except Exception:  # noqa: BLE001
                pass
        super().close()


# ════════════════════════════════════════════════════════════
#  ③ SSE（TR）：MCP SSE 传输
# ════════════════════════════════════════════════════════════


class SseLoader(Loader):
    """MCP **SSE 传输**：`GET <url>` 得到事件流，首个 `endpoint` 事件给出 POST 地址

    协议（MCP 2024-11-05 的 HTTP+SSE 传输）：
        C→S  GET  <url>            Accept: text/event-stream
        S→C  event: endpoint / data: <messages-url>
        C→S  POST <messages-url>   帧体 = JSON-RPC 请求
        S→C  event: message  / data: <JSON-RPC 响应>

    【真实性与诚实标注】代码路径真实；验证用**真实本地 HTTP 服务**
    （测试里起 `http.server`）。仓库**无**生产 SSE MCP 端点 ⇒ 生产可用性未验证。
    """

    kind = "sse"
    location = "remote"

    def __init__(self, name: str = "", *, endpoint: str = "",
                 timeout: float = 20.0, **kw: Any) -> None:
        super().__init__(name or "sse", **kw)
        self.endpoint = str(endpoint or "")
        self.timeout = float(timeout)
        self._sessions: Dict[str, "_SseSession"] = {}
        self._pool_lock = threading.RLock()

    def _do_connect(self, handle: Handle) -> None:
        url = handle.endpoint or self.endpoint
        if not url:
            raise LoaderInitError("SSE Loader 未配置 endpoint（CP_CAPABILITY_ENDPOINTS）")
        with self._pool_lock:
            if url not in self._sessions:
                sess = _SseSession(url, timeout=self.timeout,
                                   http=self._http_request)
                sess.start()
                self._sessions[url] = sess

    def _http_request(self, method: str, url: str, *,
                      headers: Optional[dict] = None,
                      text: str = "", timeout: float = 20.0,
                      stream: bool = False) -> Any:
        """统一经 `agent/web/http_client.py` 发请求（**不得**绕过 EgressGuard）

        【为什么必须复用而不是直接 `requests`】`HttpClient` 内含
        **EgressGuard 出域检查点**（`agent/web/http_client.py:100-120` 的
        `_egress_block`）。直接 `requests` 会绕过它 ⇒ 正是 `TASK-07` 要收的口子，
        也是 `TASK-05` §3 第 2 步第 2 项"必须复用 `agent/web/http_client.py`"的
        字面要求。

        【为什么非流式分支不用 `client.request(stream=True)`】`HttpClient.request`
        在 `stream=True` 时会把 `content`/`text` 置 None，**拿不到响应对象**，
        而 SSE 必须自己迭代事件行。故：
            ① 非流式 → `client.request(...)`（完整复用）；
            ② 流式   → 复现同一条出域检查 + 复用 `HttpClient` 已配置好的
                       `requests.Session`（代理/UA/Cookie 配置不丢），
                       只是额外要求原始响应对象。
        这不是"绕过"，是把 `HttpClient` 没暴露的那一个能力补在**同一条检查之后**。
        """
        client = self._client()
        if not stream:
            return client.request(method, url, headers=headers or {},
                                  data=text.encode("utf-8") if text else None,
                                  timeout=int(max(1, timeout)))
        # ① 出域检查（与 HttpClient 内的实现同源，不另立一套策略）
        try:
            from agent.guardrails.egress_guard import EgressGuard  # noqa: PLC0415
            decision = EgressGuard.precheck(method=method, url=url, headers=headers)
            if decision is not None and not bool(getattr(decision, "allowed", True)):
                raise LoaderUnavailable(
                    f"出域策略拒绝（EgressGuard）: {method} {url}")
        except LoaderUnavailable:
            raise
        except Exception:  # noqa: BLE001  守卫不可用 ⇒ 与 HttpClient 同口径放行
            pass
        # ② 复用客户端已配置的会话发流式请求
        session = getattr(client, "_session", None)  # noqa: SLF001 取已配置会话
        if session is not None:
            return session.request(method, url, headers=headers or {},
                                   timeout=timeout, stream=True)
        import requests  # noqa: PLC0415 极端兜底：HttpClient 未暴露 session 时
        return requests.request(method, url, headers=headers or {},
                                timeout=timeout, stream=True)

    def _client(self) -> Any:
        from agent.web.http_client import HttpClient  # noqa: PLC0415 惰性
        if SseLoader._http_client is None:
            SseLoader._http_client = HttpClient()
        return SseLoader._http_client

    #: 可注入的 HttpClient（测试替换用）
    _http_client: Any = None

    def _do_invoke(self, handle: Handle, args: Mapping[str, Any]) -> Any:
        url = handle.endpoint or self.endpoint
        with self._pool_lock:
            sess = self._sessions.get(url)
        if sess is None:
            raise LoaderUnavailable(f"SSE 会话未建立: {url}")
        resp = sess.request("tools/call", {"name": handle.capability,
                                            "arguments": dict(args)})
        if "error" in resp:
            raise LoaderUnavailable(f"JSON-RPC 错误: {resp['error']}")
        result = resp.get("result") or {}
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        raw = texts[0] if texts else ""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw, "isError": bool(result.get("isError"))}

    #: 可注入的 HttpClient（测试替换用）
    _http_client: Any = None

    def eager_init(self) -> None:
        """启动期探测（v1.4 §8.1：SSE = eager + 连接池）

        未配置端点 ⇒ **不探测**（仓库当前没有任何生产 SSE 端点，见模块 docstring
        的诚实标注）：把"没配置"当成失败会让整套能力面在默认配置下变红。
        """
        if not self.endpoint:
            return
        if not self.connect(Handle(capability="<probe>", loader=self.kind,
                                   location=self.location,
                                   endpoint=self.endpoint)):
            raise LoaderInitError(f"SSE 端点连接失败: {self.last_error or '未知原因'}")

    def close(self) -> None:
        with self._pool_lock:
            sessions, self._sessions = self._sessions, {}
        for s in sessions.values():
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        super().close()


class _SseSession:
    """一条 SSE 会话：后台线程读事件流，`request()` 同步等对应响应"""

    def __init__(self, url: str, *, timeout: float,
                 http: Callable[..., Any]) -> None:
        self.url = url
        self.timeout = float(timeout)
        self._http = http
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._post_url = ""
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._id = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="capregistry-sse")

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=self.timeout):
            raise LoaderInitError(f"SSE 握手超时（{self.timeout}s）: {self.url}")
        # 【不易·必须再查一次 `_post_url`】事件流可能**立即失败**（404 / 连接被拒），
        # 此时 `_run()` 的 `finally` 也会 `_ready.set()`（否则等待者要白等满超时）。
        # 只看 `_ready` 会把"握手失败"误判成"握手成功" ⇒ 后续每次调用都在
        # "未拿到 messages 端点"上失败，而**健康状态却显示 ready**。
        # 这正是"错误被推迟到第一次业务调用"的典型形态，必须在连接期判掉。
        if not self._post_url:
            raise LoaderInitError(
                f"SSE 握手未拿到 messages 端点（事件流可能立即失败）: {self.url}")

    def _run(self) -> None:
        try:
            resp = self._http("GET", self.url,
                              headers={"Accept": "text/event-stream",
                                       "Cache-Control": "no-cache"},
                              timeout=self.timeout, stream=True)
            if hasattr(resp, "raise_for_status"):
                resp.raise_for_status()
            event = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if self._stop.is_set():
                    break
                line = (raw or "").strip()
                if not line:
                    event = ""
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                    if event == "endpoint":
                        self._post_url = self._resolve_endpoint(data)
                        self._ready.set()
                    elif event in ("message", ""):
                        try:
                            self._queue.put(json.loads(data))
                        except json.JSONDecodeError:
                            logger.debug("[capregistry.sse] 丢弃非 JSON data: %.120s", data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[capregistry.sse] 事件流中断: %s: %s", type(exc).__name__, exc)
        finally:
            self._ready.set()   # 失败也要放行等待者（否则 start() 白等满超时）

    def _resolve_endpoint(self, data: str) -> str:
        if data.startswith(("http://", "https://")):
            return data
        from urllib.parse import urljoin
        return urljoin(self.url, data)

    def request(self, method: str, params: Optional[dict] = None) -> dict:
        if not self._post_url:
            raise LoaderUnavailable("SSE 会话未拿到 messages 端点")
        with self._lock:
            self._id += 1
            req_id = self._id
            body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                               "params": dict(params or {})}, ensure_ascii=False)
            res = self._http("POST", self._post_url,
                             headers={"Content-Type": "application/json"},
                             text=body, timeout=self.timeout)
        if isinstance(res, dict) and not res.get("ok", True):
            raise LoaderUnavailable(f"POST 失败: {res.get('error') or res.get('status_code')}")
        deadline = time.monotonic() + self.timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"SSE 响应超时（{self.timeout}s, method={method}）")
            try:
                msg = self._queue.get(timeout=min(remain, 0.5))
            except queue.Empty:
                continue
            if msg.get("id") == req_id:
                return msg
            self._queue.put(msg)

    def close(self) -> None:
        self._stop.set()


# ════════════════════════════════════════════════════════════
#  ④ HTTP（TR）：MCP streamable-HTTP（单 POST，响应可为 JSON 或 SSE 帧）
# ════════════════════════════════════════════════════════════


class HttpLoader(Loader):
    """MCP **streamable HTTP** 传输：`POST <url>` 一帧请求，响应 JSON 或 SSE

    【必须复用 HttpClient】见 `SseLoader._http_request` 的注释（EgressGuard）。
    """

    kind = "http"
    location = "remote"

    def __init__(self, name: str = "", *, endpoint: str = "",
                 timeout: float = 20.0, **kw: Any) -> None:
        super().__init__(name or "http", **kw)
        self.endpoint = str(endpoint or "")
        self.timeout = float(timeout)
        self._id = 0
        self._id_lock = threading.Lock()

    def _client(self) -> Any:
        from agent.web.http_client import HttpClient  # noqa: PLC0415 惰性
        if HttpLoader._http_client is None:
            HttpLoader._http_client = HttpClient()
        return HttpLoader._http_client

    _http_client: Any = None

    def _do_connect(self, handle: Handle) -> None:
        url = handle.endpoint or self.endpoint
        if not url:
            raise LoaderInitError("HTTP Loader 未配置 endpoint（CP_CAPABILITY_ENDPOINTS）")
        # eager 校验：先做一次 initialize，把"端点根本不可用"在连接期暴露出来，
        # 而不是等到第一次业务调用才失败（那是 v1.4 §8.1 说的 eager 策略）
        self._rpc(url, "initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "capregistry-loader", "version": "1.0.0"},
            "capabilities": {}})

    def _rpc(self, url: str, method: str, params: Optional[dict]) -> dict:
        with self._id_lock:
            self._id += 1
            req_id = self._id
        body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                           "params": dict(params or {})}, ensure_ascii=False)
        res = self._client().request(
            "POST", url, headers={"Content-Type": "application/json",
                                  "Accept": "application/json, text/event-stream"},
            data=body.encode("utf-8"), timeout=int(max(1, self.timeout)))
        if not isinstance(res, dict):
            raise LoaderUnavailable("HTTP 客户端返回非 dict")
        if not res.get("ok"):
            raise LoaderUnavailable(
                f"HTTP 请求失败: status={res.get('status_code')} "
                f"error={res.get('error') or ''}")
        text = str(res.get("text") or "")
        ctype = str((res.get("headers") or {}).get("Content-Type") or "")
        if "text/event-stream" in ctype:
            return self._parse_sse(text, req_id)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoaderUnavailable(f"HTTP 响应不是合法 JSON: {exc}") from exc

    @staticmethod
    def _parse_sse(text: str, req_id: int) -> dict:
        """从 SSE 文本里取 id 匹配的那一帧（无匹配 ⇒ 抛，不猜）"""
        event = ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                event = ""
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                if event in ("message", ""):
                    try:
                        msg = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == req_id:
                        return msg
        raise LoaderUnavailable("SSE 响应中没有匹配 id 的 JSON-RPC 帧")

    def _do_invoke(self, handle: Handle, args: Mapping[str, Any]) -> Any:
        url = handle.endpoint or self.endpoint
        resp = self._rpc(url, "tools/call", {"name": handle.capability,
                                             "arguments": dict(args)})
        if "error" in resp:
            raise LoaderUnavailable(f"JSON-RPC 错误: {resp['error']}")
        result = resp.get("result") or {}
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        raw = texts[0] if texts else ""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw, "isError": bool(result.get("isError"))}

    def eager_init(self) -> None:
        """启动期探测（v1.4 §8.1：HTTP = eager + 连接池）"""
        if not self.endpoint:
            return
        if not self.connect(Handle(capability="<probe>", loader=self.kind,
                                   location=self.location,
                                   endpoint=self.endpoint)):
            raise LoaderInitError(f"HTTP 端点连接失败: {self.last_error or '未知原因'}")


# ════════════════════════════════════════════════════════════
#  LoaderManager：逐 Loader 独立初始化（**D4/E5 的落点**）
# ════════════════════════════════════════════════════════════


class LoaderManager:
    """把 `CapabilityRecord` 按 `location` 分派到对应 Loader

    【D4 的关键实现】每个 Loader 的构造与连接都在**独立的 try/except** 里，
    失败只记 `init_errors` + 结构化告警，**绝不冒泡**。于是：

        某个 Loader 初始化失败 ⇒ `app_server.py` 照常启动；
        该 Loader 承载的能力标 `unhealthy`（`/capabilities/tools?healthy_only=true`
        里被过滤掉），其余链路完全不受影响。
    """

    def __init__(self, *, now: Optional[Callable[[], float]] = None) -> None:
        self._now = now or time.monotonic
        self.loaders: Dict[str, Loader] = {}
        self.init_errors: List[Dict[str, str]] = []
        self._lock = threading.RLock()
        self._endpoints: Dict[str, Dict[str, str]] = {}
        self._build()

    # ── 构造（逐 Loader 独立 try/except）──

    def _build(self) -> None:
        disabled = {
            x.strip().lower()
            for x in str(os.environ.get("CP_CAPABILITY_LOADER_DISABLED", "") or "").split(",")
            if x.strip()
        }
        self._endpoints = self._parse_endpoints(
            os.environ.get("CP_CAPABILITY_ENDPOINTS", "") or "")
        try:
            stdio_server = str(os.environ.get(
                "CP_CAPABILITY_STDIO_SERVER", StdioLoader.DEFAULT_SERVER) or "")
        except Exception:  # noqa: BLE001
            stdio_server = StdioLoader.DEFAULT_SERVER
        try:
            prewarm = int(os.environ.get("CP_CAPABILITY_STDIO_PREWARM", "0") or 0)
        except (TypeError, ValueError):
            prewarm = 0
        try:
            timeout = float(os.environ.get("CP_CAPABILITY_HTTP_TIMEOUT", "20") or 20)
        except (TypeError, ValueError):
            timeout = 20.0

        specs: List[Tuple[str, Callable[[], Loader]]] = [
            ("local", lambda: LocalLoader("local", now=self._now)),
            ("stdio", lambda: StdioLoader("stdio", server=stdio_server,
                                          prewarm=prewarm, timeout=timeout,
                                          now=self._now)),
            ("sse", lambda: SseLoader("sse",
                                      endpoint=self._endpoints.get("sse", {}).get("default", ""),
                                      timeout=timeout, now=self._now)),
            ("http", lambda: HttpLoader("http",
                                        endpoint=self._endpoints.get("http", {}).get("default", ""),
                                        timeout=timeout, now=self._now)),
        ]
        for kind, factory in specs:
            if kind in disabled:
                self.init_errors.append({
                    "loader": kind, "error": "被 CP_CAPABILITY_LOADER_DISABLED 显式禁用"})
                logger.info("[capregistry] Loader %s 已被开关禁用（不注册）", kind)
                continue
            try:
                self.loaders[kind] = factory()
            except Exception as exc:  # noqa: BLE001  单个 Loader 构造失败 ⇒ 只记不抛
                msg = f"{type(exc).__name__}: {exc}"
                self.init_errors.append({"loader": kind, "error": msg})
                logger.error("[capregistry] Loader %s 初始化失败（不影响启动）: %s",
                             kind, msg)
                continue
            # ── 初始化探测（eager）──
            # 【不易·为什么 b_ 构造之后还要 probe】只把 Loader **建出来**而不做任何
            #   初始化动作，"初始化失败"就永远不会在启动期暴露 —— 那就把
            #   `TASK-05` E5（"故意让一个 Loader 初始化失败，平台仍能启动"）
            #   变成了一个**无法观测**的性质。v1.4 §8.1 的加载策略矩阵也要求
            #   Stdio 预热、SSE/HTTP eager ⇒ 启动期本来就该有一次真实探测。
            # 【D4】探测同样逐个 try/except：失败只记 `init_errors` 并把该 Loader
            #   标成 unhealthy，**绝不冒泡**。
            try:
                self.loaders[kind].eager_init()
            except Exception as exc:  # noqa: BLE001
                msg = f"初始化探测失败: {type(exc).__name__}: {exc}"
                self.init_errors.append({"loader": kind, "error": msg})
                logger.error("[capregistry] Loader %s %s（该 Loader 承载的能力标 "
                             "unhealthy，平台照常启动）", kind, msg)

    @staticmethod
    def _parse_endpoints(raw: str) -> Dict[str, Dict[str, str]]:
        """`kind:name=url,kind:name=url` ⇒ `{kind: {name: url}}`

        【为什么用一条环境变量而不是每条链路一个】D5 要求每个新开关都进设置注册表；
        开关越多，注册表与代码的漂移面越大。端点是**同类数据**，用一条表格式的
        开关表达更不容易漂移。
        """
        out: Dict[str, Dict[str, str]] = {}
        for chunk in str(raw or "").split(","):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            left, _, url = chunk.partition("=")
            kind, _, name = left.partition(":")
            kind = (kind or "http").strip().lower()
            name = (name or "default").strip() or "default"
            out.setdefault(kind, {})[name] = url.strip()
        return out

    # ── 分派 ──

    def loader_for(self, spec: Any) -> Optional[Loader]:
        """按 `location` 选 Loader；选不到返回 `None`（调用方据此报 `unhealthy`）"""
        loc = str(getattr(spec, "location", "") or "")
        if loc == "local":
            return self.loaders.get("local")
        # remote：默认 stdio（本机 stdio MCP 也是 remote，见 TASK-00 §0.4）；
        # 显式登记了端点时按端点类型选
        name = str(getattr(spec, "tool_name", "") or "")
        for kind in ("http", "sse"):
            table = self._endpoints.get(kind) or {}
            if name in table:
                return self.loaders.get(kind)
        return self.loaders.get("stdio") or self.loaders.get("http")

    def health_of(self, name: str) -> str:
        """`CapabilityRegistry` 的 `health_provider`（**只读**）

        未连接/未调用的 Loader 返回 `"unknown"`（按"可用"处理，见
        `view.is_healthy` 的注释）；任一 Loader 进入退避/熔断则返回 `"unhealthy"`。

        【为什么是 Loader 级而不是能力级】传输层健康是**连接**属性：一条 stdio
        进程挂了，挂在它上面的所有远端能力一起不可用。逐能力维护健康需要每能力
        一条独立连接 —— 那是 100+ 个常驻进程，与 §2.4 的资源约束冲突。
        故取"Loader 级健康"作为能力健康的上界，并**如实标注口径**。
        """
        with self._lock:
            loaders = list(self.loaders.values())
        worst = "unknown"
        for ld in loaders:
            st = ld.health()
            if st == "unhealthy":
                return "unhealthy"
            if st == "healthy" and worst == "unknown":
                worst = "healthy"
        return worst

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "loaders": {k: v.describe() for k, v in self.loaders.items()},
                "disabled_or_failed": list(self.init_errors),
                "endpoints": {k: sorted(v) for k, v in self._endpoints.items()},
            }

    def close(self) -> None:
        with self._lock:
            loaders = list(self.loaders.values())
        for ld in loaders:
            try:
                ld.close()
            except Exception:  # noqa: BLE001
                pass


_MGR_LOCK = threading.Lock()
_MGR: Dict[str, Any] = {"mgr": None}


def get_loader_manager(*, force: bool = False) -> LoaderManager:
    """进程内单例（**构造失败也返回一个空的 manager**，绝不让启动链路失败）"""
    with _MGR_LOCK:
        if force or _MGR["mgr"] is None:
            try:
                _MGR["mgr"] = LoaderManager()
            except Exception as exc:  # noqa: BLE001 兜底：连 manager 都建不出来
                logger.error("[capregistry] LoaderManager 构造异常（返回空 manager）: %s",
                             exc, exc_info=True)
                empty = LoaderManager.__new__(LoaderManager)
                empty._now = time.monotonic  # noqa: SLF001
                empty.loaders = {}
                empty.init_errors = [{"loader": "*",
                                      "error": f"{type(exc).__name__}: {exc}"}]
                empty._lock = threading.RLock()  # noqa: SLF001
                empty._endpoints = {}
                _MGR["mgr"] = empty
        return _MGR["mgr"]


def reset_loader_manager() -> None:
    """清空单例并关闭所有 Loader（测试用）"""
    with _MGR_LOCK:
        mgr = _MGR["mgr"]
        if mgr is not None:
            try:
                mgr.close()
            except Exception:  # noqa: BLE001
                pass
        _MGR["mgr"] = None
