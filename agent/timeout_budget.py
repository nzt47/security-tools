"""超时上界与重试预算（TASK-08 子工作流 D：E1j / E15 / E1e / E1e2）

【不易（为什么需要这个模块）】
    TASK-08 审计发现两条**无超时上界**的执行路径，它们使「最坏耗时」无法被度量：

    1. `agent/skills_mgmt/mcp_adapter.py::_list_tools/_call_tool` 直接裸调
       `session.initialize()` / `session.list_tools()` / `session.call_tool()`，
       而 `McpServerConfig.timeout`（默认 30）**从未传给 SDK** ⇒ 配置了等于没配。
    2. `agent/tools/__init__.py::call()` 里 `result = tool["handler"](**params)`
       无包装无上界 ⇒ handler 一旦挂死，调用方**无限阻塞**。
       （`agent/tools/code_tools.py` 的 schema 自述「不设置则不限时」，正是这条路径的注脚。）

    同时重试在**三层**各自为政地放大：
        · `agent/tool_calling.py` 工具循环内每轮 LLM 调用重试 3 次（无抖动）
        · `agent/error_handler.py::execute_with_retry` 又重试 `max_retries+1` = 4 次（有抖动）
        · `mcp_services/mcp_client.py::retry_on_failure` 再重试 N 次（无抖动）
    三者相乘即「重试放大」。本模块把它们收敛到**同一份预算**上。

【变易（改哪里）】
    新增/调整开关 → 在 `agent/settings/registry.py` 的 `_REGISTRY_ROWS` 加一行；
    本模块只暴露「读取 + 判定」的纯函数，不含业务分支。

【静默失败的边界（D4 纪律）】
    本模块只依赖标准库。任何解析异常都**退回默认值**而不是抛出 —— 启动链路
    （`app_server.py` → 路由模块）不允许因一个开关读错而失败。
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import os
import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

# ════════════════════════════════════════════════════════════
#  开关（env 读取点必须落在**扫描根内**：`scripts/scan_settings.py` 的零缺口守卫
#  自 L5（2026-09-23）起扫描**全仓生产代码**（顶层包 + mcp_services + 仓库根入口脚本，
#  见该文件 DEFAULT_ROOTS）——但 scripts/ tests/ 等仍不在范围内；
#  读点放在范围外会让注册表条目变成「没人读的开关」而被 test_settings_registry 反向判红）
# ════════════════════════════════════════════════════════════

#: 工具 handler 的墙钟上界（秒）。**0 表示不限**（保留旧行为，用于紧急回滚）。
#:
#: 【为什么默认是 1800 而不是看着更「好看」的 120】
#: 仓库里各工具**自述**的上限是分层的，实测（grep 证据）：
#:     test_tools  timeout_sec 默认 300 / 最大 900
#:     lint_tools  timeout_sec 默认 180 / 上限 600
#:     git_tools   timeout_sec 默认 60  / 最大 120
#:     shell_tools timeout 被硬夹在 1–120
#:     code_tools  显式「不设置则不限时」（用户可传任意 timeout）
#: 若把全局上界压到 120，会**削掉 lint/test 这两条已文档化的长任务契约**
#: （它们是子进程 + 编译/测试，600–900 秒是正常量级）。因此这里取
#: **严格高于一切已文档化上限**的 1800 秒：它不裁掉任何既有契约，只把
#: 「无限」变成「有界」，正是 E1j 要的那一件事。
_TOOL_HANDLER_TIMEOUT_ENV = "CP_TOOL_HANDLER_TIMEOUT_SEC"
_TOOL_HANDLER_TIMEOUT_DEFAULT = 1800.0

#: MCP adapter 调用上界（秒）；仅当 `McpServerConfig.timeout <= 0` 时兜底。
_MCP_CALL_TIMEOUT_ENV = "CP_MCP_CALL_TIMEOUT_SEC"
_MCP_CALL_TIMEOUT_DEFAULT = 30.0

#: 跨层**总重试预算**（次）。语义：只计「重试」，不计每次逻辑调用的首次尝试；
#: 因此 21 轮工具循环各成功一次的正常路径消耗 0，不会被这个预算误伤。
_RETRY_MAX_ATTEMPTS_ENV = "CP_RETRY_MAX_ATTEMPTS"
_RETRY_MAX_ATTEMPTS_DEFAULT = 6

#: 重试**窗口**总耗时上界（秒）。时钟自**第一次重试**起算（惰性启动），
#: 不从任务开始算 —— 否则一个跑了 10 分钟后才遇到瞬时抖动的大任务会被误判耗尽。
_RETRY_DEADLINE_ENV = "CP_RETRY_DEADLINE_SEC"
_RETRY_DEADLINE_DEFAULT = 60.0

#: 工具调用重试与 MCP 重试的抖动系数（±10%）。
#:
#: 【分层纪律，不可笼统表述】LLM 主链路**已有**抖动：
#: `memory/llm_service.py` 的 `with_retry(..., jitter_factor=0.1)` →
#: `agent/error_handler.py::RetryPolicy.calculate_delay` 用
#: `random.uniform(1-j, 1+j)` 实现。缺口**只在**「工具调用重试」
#: （`tool_calling.py`：`delay = 2 ** retry_attempt`）与「MCP 重试」
#: （`mcp_client.py`：`current_delay *= backoff_factor`）两处。默认值与主链路
#: 对齐为 0.1，使整链抖动口径一致。
_RETRY_JITTER_ENV = "CP_RETRY_JITTER_FACTOR"
_RETRY_JITTER_DEFAULT = 0.1

#: handler 外层上界相对「工具自述 timeout」的宽限（秒）
#: 工具自己的超时是**内部**判定，子进程回收/序列化会略晚于它才返回，
#: 外层若与内层等值会在正常路径上抢先误杀。
_OUTER_GRACE_SEC = 15.0

#: 感知「工具自述标量超时」的参数名。**故意不含 `timeout_seconds`**：
#: `subagent`/`fan_out` 的 `timeout_seconds` 是**每个子任务的**时长，
#: 整批总耗时合法地远大于它，据此收紧外层上界会误杀。
_SCALAR_TIMEOUT_PARAMS = ("timeout", "timeout_sec")


def _env_float(name: str, default: float) -> float:
    """读一个 float 开关；解析失败一律回默认（不得因开关读错而中断启动）"""
    try:
        raw = os.environ.get(name, "")
        if raw is None or str(raw).strip() == "":
            return float(default)
        val = float(str(raw).strip())
        return val if val >= 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    """读一个 int 开关；解析失败一律回默认"""
    try:
        raw = os.environ.get(name, "")
        if raw is None or str(raw).strip() == "":
            return int(default)
        val = int(float(str(raw).strip()))
        return val if val >= 0 else int(default)
    except (TypeError, ValueError):
        return int(default)


def tool_handler_timeout() -> float:
    """工具 handler 的全局墙钟上界（秒）；0 = 不限"""
    return _env_float(_TOOL_HANDLER_TIMEOUT_ENV, _TOOL_HANDLER_TIMEOUT_DEFAULT)


def mcp_call_timeout(config: Any = None) -> float:
    """MCP 调用上界（秒）

    优先取 server 配置上的 `timeout` —— 这正是 E1j 的要点：
    配置项必须**真的生效**，而不是被一个常量悄悄顶掉。
    """
    configured = 0.0
    try:
        configured = float(getattr(config, "timeout", 0) or 0)
    except (TypeError, ValueError):
        configured = 0.0
    if configured > 0:
        return configured
    return _env_float(_MCP_CALL_TIMEOUT_ENV, _MCP_CALL_TIMEOUT_DEFAULT)


def retry_jitter_factor() -> float:
    """工具调用 / MCP 重试的抖动系数"""
    return _env_float(_RETRY_JITTER_ENV, _RETRY_JITTER_DEFAULT)


def jittered_delay(base_delay: float, factor: Optional[float] = None) -> float:
    """给退避延迟加去相关抖动

    Why 抖动：无抖动时多个失败调用会**同相位**重试，把瞬时故障放大成
    重试风暴（thundering herd）。抖动把重试点打散。
    系数口径与 LLM 主链路（`error_handler.RetryPolicy`）保持一致。
    """
    try:
        base = float(base_delay)
    except (TypeError, ValueError):
        return 0.0
    if base <= 0:
        return 0.0
    f = retry_jitter_factor() if factor is None else max(0.0, float(factor))
    if f <= 0:
        return base
    return max(0.0, base * random.uniform(1.0 - f, 1.0 + f))


def resolve_tool_handler_timeout(tool: Optional[dict], params: Optional[dict] = None) -> float:
    """算出本次 handler 调用的上界（秒）；0 = 不限

    分层规则（内层优先，取更紧但**不裁掉工具自述契约**的那个）：
      1. 全局天花板 `CP_TOOL_HANDLER_TIMEOUT_SEC`（默认 1800，0 = 不限）；
      2. 若调用参数里带了工具自述的标量超时（`timeout` / `timeout_sec`），
         取 `min(天花板, 自述值 + 宽限)` —— 外层跟随工具的承诺收紧；
      3. 全局关闭（0）时**一律不限**，保留旧行为以便回滚。
    """
    ceiling = tool_handler_timeout()
    if ceiling <= 0:
        return 0.0
    if params:
        for key in _SCALAR_TIMEOUT_PARAMS:
            raw = params.get(key)
            if raw is None:
                continue
            try:
                declared = float(raw)
            except (TypeError, ValueError):
                continue
            if declared > 0:
                return min(ceiling, declared + _OUTER_GRACE_SEC)
    return ceiling


# ════════════════════════════════════════════════════════════
#  跨层重试预算
# ════════════════════════════════════════════════════════════

class RetryBudget:
    """一次任务内**跨层共享**的重试预算（次数 + 窗口耗时）

    为什么用「预算对象」而不是给每层配一个 max_retries：
        三层各自配 N 的结果是 N³ 放大（工具 3 × 错误处理 4 × MCP 3），
        因为**每一层都以为自己是唯一的重试者**。共享一份预算后，
        「总重试次数」成为任务级不变量，与层数解耦。

    【时钟注入】`time_fn` 可注入，使测试无需真的 sleep 即可断言窗口耗尽
    （D12：时间类断言注入时钟，不真等）。
    """

    __slots__ = ("max_retries", "deadline_sec", "_time_fn", "_used", "_started_at", "_lock", "_denied")

    def __init__(self, max_retries: Optional[int] = None,
                 deadline_sec: Optional[float] = None,
                 time_fn: Optional[Callable[[], float]] = None):
        self.max_retries = (_env_int(_RETRY_MAX_ATTEMPTS_ENV, _RETRY_MAX_ATTEMPTS_DEFAULT)
                            if max_retries is None else max(0, int(max_retries)))
        self.deadline_sec = (_env_float(_RETRY_DEADLINE_ENV, _RETRY_DEADLINE_DEFAULT)
                             if deadline_sec is None else max(0.0, float(deadline_sec)))
        self._time_fn = time_fn or time.monotonic
        self._used = 0
        self._started_at: Optional[float] = None
        self._lock = threading.Lock()
        self._denied = 0

    # ── 状态 ──
    @property
    def used(self) -> int:
        return self._used

    @property
    def denied(self) -> int:
        """被预算拒绝的重试次数（可观测：>0 意味着确实发生了放大）"""
        return self._denied

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._is_exhausted_locked()

    def _is_exhausted_locked(self) -> bool:
        if self._used >= self.max_retries:
            return True
        if self.deadline_sec > 0 and self._started_at is not None:
            if (self._time_fn() - self._started_at) >= self.deadline_sec:
                return True
        return False

    # ── 消费 ──
    def try_consume(self, layer: str = "") -> bool:
        """申请一次重试额度；允许返回 True，预算耗尽返回 False

        调用方在拿到 False 时**必须放弃重试并上抛/返回 `deadline_exceeded`**，
        不得再自行重试 —— 否则预算形同虚设。
        """
        with self._lock:
            if self._started_at is None:
                # 惰性启动窗口：第一次重试才起算，避免长任务后半段的瞬时抖动被误杀
                self._started_at = self._time_fn()
            if self._is_exhausted_locked():
                self._denied += 1
                return False
            self._used += 1
            return True

    def snapshot(self) -> Dict[str, Any]:
        """只读快照（可观测/审计用；不含任何活对象引用）"""
        with self._lock:
            elapsed = 0.0 if self._started_at is None else (self._time_fn() - self._started_at)
            return {
                "max_retries": self.max_retries,
                "used": self._used,
                "denied": self._denied,
                "deadline_sec": self.deadline_sec,
                "elapsed_sec": round(elapsed, 3),
            }


#: 当前线程/任务的重试预算。用 ContextVar 以便随协程与显式 context 拷贝传播。
_CURRENT_BUDGET: contextvars.ContextVar[Optional[RetryBudget]] = contextvars.ContextVar(
    "yunshu_retry_budget", default=None)


def current_budget() -> Optional[RetryBudget]:
    return _CURRENT_BUDGET.get()


def use_budget(budget: RetryBudget):
    """把预算设为当前（返回 token，配合 reset 使用）"""
    return _CURRENT_BUDGET.set(budget)


def reset_budget(token) -> None:
    try:
        _CURRENT_BUDGET.reset(token)
    except (ValueError, LookupError):        # 跨上下文 reset：忽略即可
        pass


def consume_retry(layer: str = "") -> bool:
    """从**当前**预算申请一次重试额度

    没有活动预算时返回 True（= 不限），以保证未接线预算的调用方
    （如独立的 MCP 客户端、无任务的单次调用）行为**与改动前一致**（D2）。
    """
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        return True
    return budget.try_consume(layer)


# ════════════════════════════════════════════════════════════
#  有界调用（handler / MCP 两条路径共用）
# ════════════════════════════════════════════════════════════

class HandlerTimeout(TimeoutError):
    """有界调用超时（继承 TimeoutError，便于既有 `except TimeoutError` 兼容）"""

    def __init__(self, label: str, timeout_sec: float):
        super().__init__(f"{label} 超过超时上界 {timeout_sec:.1f}s 未返回")
        self.label = label
        self.timeout_sec = timeout_sec


def call_with_timeout(fn: Callable[..., Any], timeout_sec: float, *,
                      args: Tuple[Any, ...] = (),
                      kwargs: Optional[Dict[str, Any]] = None,
                      label: str = "call") -> Tuple[bool, Any]:
    """在墙钟上界内调用 `fn`；超时返回 `(False, HandlerTimeout)` 而不阻塞

    【为什么用 daemon 线程而不是 ThreadPoolExecutor】
        `ThreadPoolExecutor` 的工作线程在 CPython 3.9+ 是**非 daemon** 的，
        且解释器退出时 `concurrent.futures.thread` 的 atexit 钩子会
        `join()` 所有工作线程 —— 一个挂死的 handler 会**连进程退出一起拖住**，
        直接把「不阻塞」的修复变成「阻塞关机」。裸 daemon 线程无此问题。

    【如实声明能力边界】
        线程超时**只解除等待，不杀死执行体**。真正卡在 C 扩展/阻塞 IO 里的
        handler 线程会作为 daemon 线程继续存在直到进程退出。这是 Python 的
        固有限制；本函数保证的是「调用方**有界返回**结构化错误」，而不是
        「强制终止业务代码」。需要硬杀的场景必须由工具自己在子进程里做
        （仓库里 `shell_tools` / `test_tools` 等已经这么做）。

    【协程 handler】
        若 `fn` 返回协程（async 工具），在线程内用 `asyncio.run` +
        `asyncio.wait_for` 真正取消它 —— 这种情况是能**真取消**的。

    Returns:
        (True, 返回值) 或 (False, HandlerTimeout)
    """
    kw = kwargs or {}
    if not timeout_sec or timeout_sec <= 0:
        return True, fn(*args, **kw)

    box: Dict[str, Any] = {}
    done = threading.Event()
    # 把当前上下文（含重试预算）显式拷进工作线程：ContextVar 默认不跨线程传播
    ctx = contextvars.copy_context()

    def _run() -> None:
        try:
            result = fn(*args, **kw)
            if inspect.iscoroutine(result):
                result = asyncio.run(asyncio.wait_for(result, timeout=timeout_sec))
                # 同步 API 调用方拿到的是「已求值的返回值」
                if hasattr(result, "__await__"):
                    result = None
            box["value"] = result
        except BaseException as exc:      # noqa: BLE001  原样回抛给调用方
            box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=ctx.run, args=(_run,),
                              name=f"cp-bounded-{label}", daemon=True)
    try:
        worker.start()
    except Exception as exc:              # 线程都起不来 ⇒ 退化为同步调用（不阻断业务）
        _ = exc
        return True, fn(*args, **kw)

    if not done.wait(timeout_sec):
        return False, HandlerTimeout(label, float(timeout_sec))
    if "error" in box:
        raise box["error"]
    return True, box.get("value")


def timeout_error_payload(tool_name: str, timeout_sec: float,
                          error_code: str = "timeout") -> Dict[str, Any]:
    """超时的**结构化**错误载荷（E1j 要求：返回错误而不是无限阻塞）

    形状与 `agent/tools/__init__.py` 既有拒绝分支保持一致
    （`{"ok": False, "error": ...}`），使上层无需为超时单开一条分支。
    """
    return {
        "ok": False,
        "error": (f"工具 '{tool_name}' 超过超时上界 {timeout_sec:.1f}s 未返回，"
                  f"已中止等待（可用 CP_TOOL_HANDLER_TIMEOUT_SEC 调整或置 0 关闭）"),
        "error_code": error_code,
        "tool": tool_name,
        "timeout_sec": timeout_sec,
    }
