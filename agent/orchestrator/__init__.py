"""
orchestrator — 云枢主编排层

职责划分:
- Orchestrator: 消息路由、工具调用协调、结果聚合
- LifecycleManager: 系统初始化、组件组装、生命周期管理
- TaskDispatcher: 任务调度与超时控制

设计原则: 主 Agent 轻量化，专注理解用户意图、任务拆解和结果整合。
"""

# PEP 562 模块级懒加载: 仅在访问具体符号时才导入子模块.
# 不变量(不易): orchestrator/__init__.py 顶层不再拉入 lifecycle_manager→digital_life
#   重依赖链, 避免与 digital_life.py:369 `from agent.orchestrator import ...` 形成循环导入.
#   循环链(修复前): __init__→lifecycle_manager→digital_life→__init__(未完成) → ImportError.
#   修复后: __init__ 顶层零导入, digital_life 通过 __getattr__ 按需解析子模块,
#   此时 digital_life 模块级符号(369 行前已定义)可被 lifecycle_manager/orchestrator 安全获取.
# 向后兼容: `from agent.orchestrator import Orchestrator, LifecycleManager, ...` 仍可用,
#   首次访问时懒加载并缓存到 globals(), 后续访问零开销.
_PKG = __name__  # "agent.orchestrator"

# 符号名 → (来源子模块路径, 符号名)
_LAZY_IMPORTS = {
    "LifecycleManager": (f"{_PKG}.lifecycle_manager", "LifecycleManager"),
    "TaskDispatcher": (f"{_PKG}.task_dispatcher", "TaskDispatcher"),
    "Orchestrator": (f"{_PKG}.orchestrator", "Orchestrator"),
}


def __getattr__(name):
    """PEP 562: 仅在访问时才导入子模块, 避免 import agent.orchestrator 触发循环依赖."""
    if name in _LAZY_IMPORTS:
        import importlib
        module_path, attr_name = _LAZY_IMPORTS[name]
        attr = getattr(importlib.import_module(module_path), attr_name)
        globals()[name] = attr  # 缓存到全局, 后续访问零开销
        return attr
    raise AttributeError(f"module {_PKG!r} has no attribute {name!r}")


def __dir__():
    """补全 dir(agent.orchestrator), 让懒加载符号可被发现 (REPL/IDE 自动补全兼容)."""
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


__all__ = [
    "LifecycleManager",
    "TaskDispatcher",
    "Orchestrator",
]
