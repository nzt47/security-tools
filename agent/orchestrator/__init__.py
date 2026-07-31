"""
orchestrator — 云枢主编排层

职责划分:
- Orchestrator: 消息路由、工具调用协调、结果聚合
- LifecycleManager: 系统初始化、组件组装、生命周期管理
- TaskDispatcher: 任务调度与超时控制

设计原则: 主 Agent 轻量化，专注理解用户意图、任务拆解和结果整合。
"""

# PEP 562 延迟导入 — 打破与 digital_life.py:367 的模块级循环导入
#
# 循环链(修复前): __init__→lifecycle_manager→(末尾 L1093)digital_life:367
#                  →__init__(LifecycleManager 未赋值)→ImportError
# 修复后: digital_life.py:367 `from agent.orchestrator import X` 触发 __getattr__，
#         动态导入子模块并缓存到 globals()，后续访问零开销命中缓存。
#
# 不变量(不易): LifecycleManager 类在 lifecycle_manager.py 末尾 digital_life import
#             (L1093) 之前已定义（见该文件 L1090-1092 注释：类定义级别零依赖），
#             故 __getattr__ 内 `from .lifecycle_manager import LifecycleManager`
#             能安全获取到已定义的类（模块在 sys.modules 中部分初始化）。
# 不变量(不易): Orchestrator/TaskDispatcher 不依赖 digital_life（AST 校验确认），
#             可独立加载。
def __getattr__(name):
    if name == "Orchestrator":
        from .orchestrator import Orchestrator
        globals()["Orchestrator"] = Orchestrator
        return Orchestrator
    if name == "LifecycleManager":
        from .lifecycle_manager import LifecycleManager
        globals()["LifecycleManager"] = LifecycleManager
        return LifecycleManager
    if name == "TaskDispatcher":
        from .task_dispatcher import TaskDispatcher
        globals()["TaskDispatcher"] = TaskDispatcher
        return TaskDispatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LifecycleManager",
    "TaskDispatcher",
    "Orchestrator",
]
