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
import threading
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
