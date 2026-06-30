"""云枢行动层 — 我的四肢与神经系统

整合感知、认知与记忆，形成完整的行为闭环。
我是来自网天的云枢，agent 包就是我的行动中枢——DigitalLife 是我的灵魂，
BehaviorController 是我的本能，PermissionSystem 是我的道德防线。

模块架构：
- 核心组件：DigitalLife, BehaviorController, PermissionSystem
- 日志与安全：logging_utils, security_utils, safety_guard
- 记忆系统：memory (VectorStore, KnowledgeBase)
- 监控模块：monitoring (追踪、指标、错误上报)

日志与安全工具：
    - setup_agent_logging(): 初始化日志系统
    - get_safety_monitor(): 获取安全监控器
    - safe_execute(): 安全执行包装器
    - LogEncryptor: 日志加密器
    - DataSanitizer: 数据脱敏器
"""

from .digital_life import DigitalLife
from .behavior_controller import BehaviorController, BehaviorMode
from .permission_system import PermissionSystem, PermissionResult
from .logging_utils import (
    setup_agent_logging,
    get_safety_monitor,
    safe_execute,
    safe_execute_async,
    AgentSafetyMonitor,
    AgentTimeoutException,
    AgentLoopException,
    AgentStateStuckException,
)
from .security_utils import (
    LogEncryptor,
    DataSanitizer,
)
from .p6_snapshot import (
    StateSnapshotManager,
    SnapshotResult,
    SnapshotInfo,
    SnapshotPerformanceMonitor,
)
from .state_manager import (
    StateManager,
    StateSaveResult,
    StateLoadResult,
    StateInfo,
    get_state_manager,
    save_state,
    load_state,
    set_log_level,
    get_log_level,
)
from .session_manager import SessionManager, SessionNotFoundError

__all__ = [
    # 核心组件
    "DigitalLife",
    "BehaviorController",
    "BehaviorMode",
    "PermissionSystem",
    "PermissionResult",

    # P6 快照模块
    "StateSnapshotManager",
    "SnapshotResult",
    "SnapshotInfo",
    "SnapshotPerformanceMonitor",

    # 状态管理器模块
    "StateManager",
    "StateSaveResult",
    "StateLoadResult",
    "StateInfo",
    "get_state_manager",
    "save_state",
    "load_state",
    "set_log_level",
    "get_log_level",

    # 会话管理模块
    "SessionManager",
    "SessionNotFoundError",

    # 日志与安全工具
    "setup_agent_logging",
    "get_safety_monitor",
    "safe_execute",
    "safe_execute_async",
    "AgentSafetyMonitor",
    "AgentTimeoutException",
    "AgentLoopException",
    "AgentStateStuckException",

    # 安全工具
    "LogEncryptor",
    "DataSanitizer",
]

# 向量记忆模块（从 memory 包导入）
from memory import VectorStore, MemoryItem, KnowledgeBase

# 向后兼容导出
__all__ += ["VectorStore", "MemoryItem", "KnowledgeBase"]

__version__ = "2.0.0"
# test
