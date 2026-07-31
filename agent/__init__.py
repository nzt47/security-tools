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

# PEP 562 模块级懒加载: 仅在访问具体符号时才导入重依赖.
# 不变量(不易): agent/__init__.py 顶层不再拉入 digital_life→sensor→psutil、
#   memory→tiktoken 等重依赖, 让 agent.skills_mgmt 等轻量子包可独立导入
#   (CI 脚本 compare_skills_legacy_vs_repo.py 只需 file_store, 不应被整包重依赖绑架).
# 向后兼容: `from agent import DigitalLife` 仍可用, 生产环境依赖齐全时正常懒加载.
# 缓存: __getattr__ 首次解析后写入 globals(), 后续访问走正常属性查找, 零额外开销.
_PKG = __name__  # "agent"

# 符号名 → (来源模块路径, 符号名)
_LAZY_IMPORTS = {
    # 核心组件
    "DigitalLife": (f"{_PKG}.digital_life", "DigitalLife"),
    "BehaviorController": (f"{_PKG}.behavior_controller", "BehaviorController"),
    "BehaviorMode": (f"{_PKG}.behavior_controller", "BehaviorMode"),
    "PermissionSystem": (f"{_PKG}.permission_system", "PermissionSystem"),
    "PermissionResult": (f"{_PKG}.permission_system", "PermissionResult"),
    # 日志与安全工具
    "setup_agent_logging": (f"{_PKG}.logging_utils", "setup_agent_logging"),
    "get_safety_monitor": (f"{_PKG}.logging_utils", "get_safety_monitor"),
    "safe_execute": (f"{_PKG}.logging_utils", "safe_execute"),
    "safe_execute_async": (f"{_PKG}.logging_utils", "safe_execute_async"),
    "AgentSafetyMonitor": (f"{_PKG}.logging_utils", "AgentSafetyMonitor"),
    "AgentTimeoutException": (f"{_PKG}.logging_utils", "AgentTimeoutException"),
    "AgentLoopException": (f"{_PKG}.logging_utils", "AgentLoopException"),
    "AgentStateStuckException": (f"{_PKG}.logging_utils", "AgentStateStuckException"),
    # 安全工具
    "LogEncryptor": (f"{_PKG}.security_utils", "LogEncryptor"),
    "DataSanitizer": (f"{_PKG}.security_utils", "DataSanitizer"),
    # P6 快照模块
    "StateSnapshotManager": (f"{_PKG}.p6_snapshot", "StateSnapshotManager"),
    "SnapshotResult": (f"{_PKG}.p6_snapshot", "SnapshotResult"),
    "SnapshotInfo": (f"{_PKG}.p6_snapshot", "SnapshotInfo"),
    "SnapshotPerformanceMonitor": (f"{_PKG}.p6_snapshot", "SnapshotPerformanceMonitor"),
    # 状态管理器模块
    "StateManager": (f"{_PKG}.state_manager", "StateManager"),
    "StateSaveResult": (f"{_PKG}.state_manager", "StateSaveResult"),
    "StateLoadResult": (f"{_PKG}.state_manager", "StateLoadResult"),
    "StateInfo": (f"{_PKG}.state_manager", "StateInfo"),
    "get_state_manager": (f"{_PKG}.state_manager", "get_state_manager"),
    "save_state": (f"{_PKG}.state_manager", "save_state"),
    "load_state": (f"{_PKG}.state_manager", "load_state"),
    "set_log_level": (f"{_PKG}.state_manager", "set_log_level"),
    "get_log_level": (f"{_PKG}.state_manager", "get_log_level"),
    # 会话管理模块
    "SessionManager": (f"{_PKG}.session_manager", "SessionManager"),
    "SessionNotFoundError": (f"{_PKG}.session_manager", "SessionNotFoundError"),
    # 向量记忆模块 (从 memory 包导入, 该包含 tiktoken 等重依赖)
    "VectorStore": ("memory", "VectorStore"),
    "MemoryItem": ("memory", "MemoryItem"),
    "KnowledgeBase": ("memory", "KnowledgeBase"),
}


def __getattr__(name):
    """PEP 562: 仅在访问时才导入重依赖, 避免 import agent 触发整包重依赖加载.

    两层解析:
      1. _LAZY_IMPORTS 命中 → 按映射导入具体符号 (如 DigitalLife).
      2. 否则尝试按子包导入 (如 agent.orchestrator).
         对 _CIRCULAR_DEP_SUBPKGS 中的子包, 先导入 digital_life 建立正确的
         加载顺序, 否则 orchestrator↔digital_life 循环依赖无法解析
         (原 agent/__init__.py eager-import digital_life 隐式建立此顺序).
    """
    import importlib
    import importlib.util
    # 层 1: 显式符号映射
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        attr = getattr(importlib.import_module(module_path), attr_name)
        globals()[name] = attr  # 缓存到全局, 后续访问零开销
        return attr
    # 层 2: 子包 fallback (agent.orchestrator / agent.monitoring / ...)
    subpkg_path = f"{_PKG}.{name}"
    try:
        spec = importlib.util.find_spec(subpkg_path)
    except (ModuleNotFoundError, ValueError):
        spec = None
    if spec is None:
        raise AttributeError(f"module {_PKG!r} has no attribute {name!r}")
    # 子包存在: 对有循环依赖的子包, 先导入 digital_life 建立加载顺序.
    # 不变量(不易): 此处仅对 _CIRCULAR_DEP_SUBPKGS 中的子包预加载 digital_life,
    #   不影响 agent.skills_mgmt 等轻量子包的独立导入 (CI 脚本不被 psutil 绑架).
    if name in _CIRCULAR_DEP_SUBPKGS:
        importlib.import_module(f"{_PKG}.digital_life")
    subpkg = importlib.import_module(subpkg_path)
    globals()[name] = subpkg  # 缓存, 后续访问零开销
    return subpkg


# 与 digital_life 存在循环依赖的子包: 必须先加载 digital_life 才能导入.
# digital_life.py line 369: from agent.orchestrator import Orchestrator, ... (基类)
# orchestrator/lifecycle_manager.py line 38: from agent.digital_life import (...) (运行时符号)
# 原 agent/__init__.py eager-import digital_life 隐式解开此循环, PEP 562 后需显式处理.
_CIRCULAR_DEP_SUBPKGS = frozenset({"orchestrator"})


def __dir__():
    """补全 dir(agent), 让懒加载符号可被发现 (REPL/IDE 自动补全兼容)."""
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


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

    # 向量记忆模块
    "VectorStore",
    "MemoryItem",
    "KnowledgeBase",
]

__version__ = "2.0.0"
