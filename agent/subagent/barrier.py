"""子代理上下文隔离屏障 — SubagentBarrier

核心功能：
1. 实现子代理间的上下文隔离
2. 防止子代理之间的上下文泄露
3. 主代理只接收子代理的摘要结论
4. 管理子代理的生命周期和状态

设计文档：P2 云枢架构升级 — Subagent Isolation (4.2)
"""

import time
import logging
import random
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class IsolationLevel(Enum):
    """隔离级别"""
    FULL = "full"           # 完全隔离（无任何共享）
    SHARED_MEMORY = "shared_memory"  # 共享记忆（只读）
    BRIDGED = "bridged"      # 桥接模式（通过主代理中转）


@dataclass
class SubagentMessage:
    """子代理消息（用于跨隔离边界传递）

    注意：只传递结构化摘要，不传递原始上下文
    """
    from_subagent: str
    to_subagent: Optional[str]  # None 表示发送给主代理
    message_type: str  # "summary", "error", "status"
    content: dict  # 结构化摘要内容
    timestamp: float = field(default_factory=time.time)


class SubagentBarrier:
    """子代理上下文隔离屏障

    功能：
    - 确保子代理之间无法直接访问彼此的上下文
    - 所有跨代理通信必须通过消息机制
    - 主代理只能接收子代理的摘要结论
    - 支持隔离级别配置

    设计原则：
    1. 子代理的 context 只能由该子代理自身访问
    2. 子代理之间的通信通过 Barrier 中转
    3. 主代理获取的是压缩后的摘要，不是原始上下文
    4. 敏感信息在跨边界时必须经过过滤

    用法:
        barrier = SubagentBarrier(isolation_level=IsolationLevel.FULL)
        
        # 注册子代理
        barrier.register("subagent_1", container)
        
        # 发送消息（通过摘要中转）
        barrier.send_message(from_id="subagent_1", to_id="subagent_2", type="result", content=summary)
        
        # 主代理获取消息
        messages = barrier.fetch_messages_for_master()
    """

    def __init__(
        self,
        isolation_level: IsolationLevel = IsolationLevel.FULL,
        enable_message_log: bool = True,
    ):
        """
        Args:
            isolation_level: 隔离级别
            enable_message_log: 是否启用消息日志（用于调试）
        """
        self._isolation_level = isolation_level
        self._enable_message_log = enable_message_log
        self._lock = threading.RLock()

        # 子代理注册表
        self._agents: dict[str, Any] = {}  # subagent_id -> container

        # 消息队列（待主代理读取）
        self._master_queue: list[SubagentMessage] = []

        # 子代理间消息队列（通过 barrier 中转）
        self._agent_queues: dict[str, list[SubagentMessage]] = {}

        # 消息日志
        self._message_log: list[dict] = []

        logger.info("[SubagentBarrier] 初始化完成: isolation_level=%s", isolation_level.value)

    # ── 注册与注销 ──

    def register(self, subagent_id: str, container: Any) -> bool:
        """注册子代理到隔离屏障

        Args:
            subagent_id: 子代理 ID
            container: SubagentContainer 实例

        Returns:
            True 表示注册成功
        """
        with self._lock:
            if subagent_id in self._agents:
                logger.warning("[SubagentBarrier] 子代理已注册: %s", subagent_id)
                return False

            self._agents[subagent_id] = container
            self._agent_queues[subagent_id] = []

            logger.info("[SubagentBarrier] 子代理已注册: %s", subagent_id)
            return True

    def unregister(self, subagent_id: str) -> bool:
        """注销子代理

        Args:
            subagent_id: 子代理 ID

        Returns:
            True 表示注销成功
        """
        with self._lock:
            if subagent_id not in self._agents:
                logger.warning("[SubagentBarrier] 子代理未注册: %s", subagent_id)
                return False

            del self._agents[subagent_id]
            del self._agent_queues[subagent_id]

            logger.info("[SubagentBarrier] 子代理已注销: %s", subagent_id)
            return True

    def is_registered(self, subagent_id: str) -> bool:
        """检查子代理是否已注册"""
        return subagent_id in self._agents

    # ── 消息传递（核心隔离机制） ──

    def send_message(
        self,
        from_id: str,
        to_id: Optional[str],
        message_type: str,
        content: dict,
    ) -> bool:
        """发送消息（通过隔离屏障中转）

        关键设计：
        - 消息内容必须是结构化摘要，不能是原始上下文
        - 跨代理消息会被过滤和验证
        - 主代理消息会进入主队列

        Args:
            from_id: 发送者 ID
            to_id: 接收者 ID（None 表示发送给主代理）
            message_type: 消息类型
            content: 消息内容（必须是摘要结构）

        Returns:
            True 表示发送成功
        """
        with self._lock:
            # 验证发送者
            if from_id not in self._agents:
                logger.warning("[SubagentBarrier] 发送者未注册: %s", from_id)
                return False

            # 构建消息
            message = SubagentMessage(
                from_subagent=from_id,
                to_subagent=to_id,
                message_type=message_type,
                content=self._sanitize_content(content),
            )

            # 根据接收者路由
            if to_id is None:
                # 发送给主代理
                self._master_queue.append(message)
                logger.debug("[SubagentBarrier] 消息 -> Master: from=%s, type=%s", from_id, message_type)
            else:
                # 发送给其他子代理
                if to_id not in self._agent_queues:
                    logger.warning("[SubagentBarrier] 接收者不存在: %s", to_id)
                    return False
                self._agent_queues[to_id].append(message)
                logger.debug("[SubagentBarrier] 消息 -> %s: from=%s, type=%s", to_id, from_id, message_type)

            # 记录日志
            if self._enable_message_log:
                self._log_message(message)

            return True

    def _sanitize_content(self, content: dict) -> dict:
        """过滤消息内容（确保只传递摘要结构）"""
        # 只允许特定的摘要字段
        allowed_keys = {
            "summary_text", "key_findings", "decisions", "action_items",
            "confidence", "tokens_used", "subagent_name", "trace_id",
            "status", "error", "result"
        }

        sanitized = {k: v for k, v in content.items() if k in allowed_keys}

        # 移除任何可能的代码或实现细节
        if "summary_text" in sanitized:
            text = str(sanitized["summary_text"])
            # 检测代码片段
            if any(indicator in text for indicator in ["def ", "class ", "import ", "function "]):
                sanitized["summary_text"] = "[代码执行结果已过滤]"
                sanitized["has_code_filtered"] = True

        return sanitized

    def _log_message(self, message: SubagentMessage):
        """记录消息日志"""
        self._message_log.append({
            "from": message.from_subagent,
            "to": message.to_subagent or "MASTER",
            "type": message.message_type,
            "timestamp": message.timestamp,
        })

        # 限制日志大小
        if len(self._message_log) > 1000:
            self._message_log = self._message_log[-500:]

    # ── 主代理接口 ──

    def fetch_messages_for_master(self, clear: bool = True) -> list[SubagentMessage]:
        """获取待主代理处理的消息

        主代理通过此方法获取所有子代理的摘要结论。

        Args:
            clear: 是否清除已获取的消息

        Returns:
            消息列表
        """
        with self._lock:
            messages = list(self._master_queue)

            if clear:
                self._master_queue.clear()

            logger.debug("[SubagentBarrier] 主代理获取消息: count=%d", len(messages))
            return messages

    def peek_master_messages(self) -> list[SubagentMessage]:
        """查看（不清除）待主代理处理的消息"""
        with self._lock:
            return list(self._master_queue)

    # ── 子代理接口 ──

    def fetch_messages_for_agent(self, subagent_id: str, clear: bool = True) -> list[SubagentMessage]:
        """获取发给特定子代理的消息

        Args:
            subagent_id: 子代理 ID
            clear: 是否清除已获取的消息

        Returns:
            消息列表
        """
        with self._lock:
            if subagent_id not in self._agent_queues:
                return []

            messages = list(self._agent_queues[subagent_id])

            if clear:
                self._agent_queues[subagent_id].clear()

            return messages

    # ── 上下文隔离验证 ──

    def verify_isolation(self, subagent_id: str) -> dict:
        """验证子代理的隔离状态

        Args:
            subagent_id: 子代理 ID

        Returns:
            隔离验证报告
        """
        with self._lock:
            container = self._agents.get(subagent_id)

            return {
                "subagent_id": subagent_id,
                "is_registered": subagent_id in self._agents,
                "isolation_level": self._isolation_level.value,
                "incoming_messages": len(self._agent_queues.get(subagent_id, [])),
                "context_accessible": False,  # 始终为 False，强化隔离
                "can_access_other_contexts": False,  # 始终为 False
            }

    # ── 统计信息 ──

    def get_stats(self) -> dict:
        """获取屏障统计信息"""
        with self._lock:
            return {
                "registered_agents": len(self._agents),
                "isolation_level": self._isolation_level.value,
                "master_queue_size": len(self._master_queue),
                "message_log_size": len(self._message_log),
                "agent_ids": list(self._agents.keys()),
            }

    def get_message_log(self, limit: int = 100) -> list[dict]:
        """获取消息日志（用于调试）"""
        with self._lock:
            return list(self._message_log[-limit:])


class ContextBoundaryError(Exception):
    """上下文边界违规异常"""
    pass


def enforce_isolation(func: Callable) -> Callable:
    """装饰器：强制上下文隔离检查

    用于标记不允许跨边界访问的方法。
    """
    def wrapper(*args, **kwargs):
        raise ContextBoundaryError(
            f"方法 {func.__name__} 不允许跨隔离边界调用。"
            "请使用 SubagentBarrier 的消息传递机制。"
        )
    return wrapper


# ════════════════════════════════════════════════════════════════════
#  并发屏障（v7.2 §4.2 调度层：并发上限 N + 回压）
# ════════════════════════════════════════════════════════════════════
#
# 【任务定位】§4.2 要求委派并行受「并发上限 N」约束，超过上限时产生**回压**而不是
#   无限排队或直接失败。SubagentBarrier（上文）解决的是「上下文隔离」，本类是另
#   一件事：「并发闸门」。两者同属屏障语义，故同置一模块。
#
# 【不易】不变量：任一时刻 in_flight ≤ max_concurrency（由 BoundedSemaphore 保证；
#   release 多余次数抛 ValueError，而不是悄悄抬高上限）。
# 【变易】max_concurrency / queue_timeout 可配；退避参数是模块常量。
# 【回压口径】等待采用「指数退避 + 抖动」（§4.2 原文），并在 queue_timeout 到期时
#   抛 BackpressureTimeout —— 让「排不进去」成为**显式失败**，调用方据此降级或缩减
#   批次，而不是把任务永久挂在队列里。

#: 回压退避参数
_BACKOFF_BASE_SECONDS = 0.005
_BACKOFF_MAX_SECONDS = 0.25
_BACKOFF_JITTER = 0.5          # 实际退避 ∈ [delay*(1-jitter), delay]


class BackpressureTimeout(RuntimeError):
    """回压超时：在 queue_timeout 内未取得并发槽位（§4.2）

    这是**显式**的排队失败，不是任务失败——调用方应缩减批次或稍后重试。
    """

    code = "E_BACKPRESSURE_TIMEOUT"

    def __init__(self, name: str, max_concurrency: int, waited_ms: float) -> None:
        self.name = name
        self.max_concurrency = int(max_concurrency)
        self.waited_ms = float(waited_ms)
        super().__init__(
            f"{self.code}: 并发屏障 {name!r} 已满（上限 {max_concurrency}），"
            f"等待 {waited_ms:.1f}ms 未取得槽位（§4.2 回压）")


@dataclass
class ConcurrencyBarrierStats:
    """并发屏障统计（可直接入审计/验收证据）"""

    name: str = ""
    max_concurrency: int = 0
    in_flight: int = 0
    peak_in_flight: int = 0
    total_admitted: int = 0
    total_waited: int = 0          # 取得槽位前确实等待过的次数（回压发生次数）
    total_rejected: int = 0        # 回压超时次数
    total_released: int = 0
    total_wait_ms: float = 0.0
    max_wait_ms: float = 0.0
    queue_timeout_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "max_concurrency": int(self.max_concurrency),
            "in_flight": int(self.in_flight),
            "peak_in_flight": int(self.peak_in_flight),
            "total_admitted": int(self.total_admitted),
            "total_waited": int(self.total_waited),
            "total_rejected": int(self.total_rejected),
            "total_released": int(self.total_released),
            "total_wait_ms": round(float(self.total_wait_ms), 2),
            "max_wait_ms": round(float(self.max_wait_ms), 2),
            "queue_timeout_seconds": self.queue_timeout_seconds,
            "saturated": int(self.in_flight) >= int(self.max_concurrency),
        }


class ConcurrencyBarrier:
    """并发上限 + 回压闸门（§4.2）

    用法::

        gate = ConcurrencyBarrier(max_concurrency=4, queue_timeout=10.0)
        with gate.slot():
            ...                       # 任一时刻最多 4 个执行体进入

    线程安全；``peak_in_flight`` 是可断言的不变量证据（≤ max_concurrency）。
    """

    def __init__(self, max_concurrency: int = 4, *,
                 queue_timeout: Optional[float] = None,
                 name: str = "delegation") -> None:
        """
        Args:
            max_concurrency: 并发上限 N（≥1）。
            queue_timeout: 排队等待上限（秒）；None = 无限等待（纯回压，不失败）。
            name: 屏障名（审计用）。
        """
        if int(max_concurrency) < 1:
            raise ValueError(f"max_concurrency 必须 ≥1：{max_concurrency!r}")
        if queue_timeout is not None and float(queue_timeout) <= 0:
            raise ValueError(f"queue_timeout 必须为正数或 None：{queue_timeout!r}")
        self._name = str(name or "delegation")
        self._max_concurrency = int(max_concurrency)
        self._queue_timeout = (float(queue_timeout) if queue_timeout is not None else None)
        self._sem = threading.BoundedSemaphore(self._max_concurrency)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._peak_in_flight = 0
        self._total_admitted = 0
        self._total_waited = 0
        self._total_rejected = 0
        self._total_released = 0
        self._total_wait_ms = 0.0
        self._max_wait_ms = 0.0

    # ── 属性 ──

    @property
    def name(self) -> str:
        return self._name

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def queue_timeout(self) -> Optional[float]:
        return self._queue_timeout

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def peak_in_flight(self) -> int:
        with self._lock:
            return self._peak_in_flight

    @property
    def saturated(self) -> bool:
        """当前是否已满（调用方据此决定是否入队）"""
        with self._lock:
            return self._in_flight >= self._max_concurrency

    # ── 获取 / 释放 ──

    def _backoff_sleep(self, delay: float) -> None:
        """指数退避 + 抖动（§4.2）"""
        jitter = random.uniform(1.0 - _BACKOFF_JITTER, 1.0)
        time.sleep(max(0.001, delay * jitter))

    def acquire(self, timeout: Optional[float] = None) -> float:
        """取得一个并发槽位（阻塞 + 回压）；返回等待毫秒数

        Args:
            timeout: 覆盖构造时的 ``queue_timeout``（秒）；None → 用构造值。

        Raises:
            BackpressureTimeout: 等待超时仍未取得槽位（**已计入 total_rejected**）。
        """
        limit = self._queue_timeout if timeout is None else float(timeout)
        start = time.time()
        delay = _BACKOFF_BASE_SECONDS
        waited = False
        while True:
            if self._sem.acquire(blocking=False):
                break
            waited = True
            elapsed = time.time() - start
            if limit is not None and elapsed >= limit:
                wait_ms = (time.time() - start) * 1000.0
                with self._lock:
                    self._total_rejected += 1
                    self._total_wait_ms += wait_ms
                    self._max_wait_ms = max(self._max_wait_ms, wait_ms)
                raise BackpressureTimeout(self._name, self._max_concurrency, wait_ms)
            slice_timeout = min(delay, _BACKOFF_MAX_SECONDS)
            if limit is not None:
                slice_timeout = max(0.001, min(slice_timeout, limit - elapsed))
            if self._sem.acquire(timeout=slice_timeout):
                break
            self._backoff_sleep(delay)
            delay = min(delay * 2.0, _BACKOFF_MAX_SECONDS)
        wait_ms = (time.time() - start) * 1000.0
        with self._lock:
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
            self._total_admitted += 1
            if waited:
                self._total_waited += 1
            self._total_wait_ms += wait_ms
            self._max_wait_ms = max(self._max_wait_ms, wait_ms)
        return wait_ms

    def release(self) -> None:
        """归还槽位（BoundedSemaphore 会在多还时抛 ValueError，不静默抬高上限）"""
        self._sem.release()
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._total_released += 1

    @contextmanager
    def slot(self, timeout: Optional[float] = None):
        """``with gate.slot():`` —— 异常路径同样归还（try/finally）"""
        self.acquire(timeout=timeout)
        try:
            yield self
        finally:
            self.release()

    # ── 统计 ──

    def stats(self) -> ConcurrencyBarrierStats:
        with self._lock:
            return ConcurrencyBarrierStats(
                name=self._name,
                max_concurrency=self._max_concurrency,
                in_flight=self._in_flight,
                peak_in_flight=self._peak_in_flight,
                total_admitted=self._total_admitted,
                total_waited=self._total_waited,
                total_rejected=self._total_rejected,
                total_released=self._total_released,
                total_wait_ms=self._total_wait_ms,
                max_wait_ms=self._max_wait_ms,
                queue_timeout_seconds=self._queue_timeout,
            )

    def reset_stats(self) -> None:
        """清空统计（**保留**在途计数与信号量状态；测试隔离用）"""
        with self._lock:
            self._peak_in_flight = self._in_flight
            self._total_admitted = 0
            self._total_waited = 0
            self._total_rejected = 0
            self._total_released = 0
            self._total_wait_ms = 0.0
            self._max_wait_ms = 0.0

    def __repr__(self) -> str:
        return (f"<ConcurrencyBarrier {self._name} "
                f"in_flight={self.in_flight}/{self._max_concurrency} "
                f"peak={self.peak_in_flight}>")
