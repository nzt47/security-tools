# 循环依赖排查与修复指南

> 记录 `agent.orchestrator` 与 `agent.digital_life` 循环导入问题的排查过程、修复方案与预防措施。
> 适用于后续遇到类似 `ImportError: cannot import name 'X' from partially initialized module 'Y'` 时的快速诊断。

## 一、问题现象

CI 单元测试收集阶段抛出以下错误（Windows + Python 3.12 矩阵最先暴露）：

```
ImportError: cannot import name 'Orchestrator' from partially initialized module 'agent.orchestrator'
(most likely due to a circular import)
```

随后又出现：

```
AttributeError: module 'agent.orchestrator' has no attribute 'lifecycle_manager'.
Did you mean: 'LifecycleManager'?
```

**特征**：
- 本地直接 `python -c "import agent.digital_life"` 可成功，但 `pytest` 收集时失败。
- 不同 Python 版本/操作系统表现不一致（Linux 偶发，Windows 必现）。
- 错误信息指向"部分初始化的模块"，是循环导入的典型信号。

## 二、排查思路（三步定位法）

### 第 1 步：还原循环链

通过错误堆栈与源码 `from ... import ...` 语句，画出模块加载依赖图：

```
agent.orchestrator.__init__
   └─(顶层 from .lifecycle_manager import LifecycleManager)
       agent.orchestrator.lifecycle_manager
       └─(顶层 from agent.digital_life import BodySensor, ...)
           agent.digital_life
           └─(L369: from agent.orchestrator import Orchestrator, LifecycleManager, TaskDispatcher)
               agent.orchestrator.__init__  ← 尚未执行完毕！→ ImportError
```

**关键判据**：循环回到 `agent.orchestrator.__init__` 时，该模块仍在执行顶层导入，
`Orchestrator`/`LifecycleManager` 等符号尚未绑定到 `globals()`，故 `from ... import` 失败。

### 第 2 步：AST 校验符号使用范围

确认"延迟导入"是否安全——即被导入符号**仅被方法体引用**，而非用于：
- 基类声明
- 类属性 / 默认参数
- 装饰器
- 类型注解（运行时求值的）

用 AST 脚本扫描 `lifecycle_manager.py` 中所有 `from agent.digital_life import ...` 的符号，
逐一检查其出现位置是否都在 `FunctionDef` / `AsyncFunctionDef` 体内：

```python
import ast, sys

def check_runtime_only_symbols(filepath, symbols):
    """校验符号是否仅出现在函数体（运行时）而非类定义级别（加载时）。"""
    tree = ast.parse(open(filepath, encoding='utf-8').read())
    # 收集所有函数体内的名称引用
    runtime_refs = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Name):
                    runtime_refs.add(node.id)
    # 模块级/类级引用 = 全部引用 - 运行时引用
    all_refs = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    loadtime_refs = all_refs - runtime_refs
    risky = [s for s in symbols if s in loadtime_refs]
    return risky  # 空列表 = 可安全延迟导入
```

本次校验：`lifecycle_manager.py` 中 51 个 `digital_life` 符号**无一用于加载时**，
证明"移到文件末尾"是安全的。

### 第 3 步：定位"重导入"触发点

用 `grep`/`rg` 搜索所有 `from agent.orchestrator import` 与 `from agent.digital_life import`，
确认哪些是**顶层导入**（影响加载顺序）、哪些是**函数内导入**（已是延迟，安全）。

## 三、修复方案（两层防御）

### 方案 A：PEP 562 模块级懒加载（针对 `__init__.py`）

**原理**：Python 3.7+ 允许模块定义 `__getattr__`，仅在访问具体符号时才触发子模块导入，
从而打破 `__init__ → submodule → __init__` 的循环。

**实现**（[agent/orchestrator/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/__init__.py)）：

```python
_PKG = __name__  # "agent.orchestrator"

# 符号名 → (来源子模块路径, 符号名)
_LAZY_IMPORTS = {
    "LifecycleManager": (f"{_PKG}.lifecycle_manager", "LifecycleManager"),
    "TaskDispatcher":   (f"{_PKG}.task_dispatcher",   "TaskDispatcher"),
    "Orchestrator":     (f"{_PKG}.orchestrator",       "Orchestrator"),
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

__all__ = ["LifecycleManager", "TaskDispatcher", "Orchestrator"]
```

**不变量（不易）**：`orchestrator/__init__.py` 顶层**零导入**子模块，
`import agent.orchestrator` 不再触发任何重依赖链。

### 方案 B：重导入移到文件末尾（针对业务模块）

**原理**：将"会触发循环"的导入语句从文件顶部移到**所有类定义之后**。
此时模块级符号（类、函数）已绑定到 `globals()`，反向导入可安全解析。

**实现**（[agent/orchestrator/lifecycle_manager.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/lifecycle_manager.py) 文件末尾）：

```python
# ════════════════════════════════════════════════════════════════════════════
#  延迟导入: digital_life 符号（放在 LifecycleManager 类定义之后）
# ════════════════════════════════════════════════════════════════════════════
# Why: digital_life.py:369 `from agent.orchestrator import ...LifecycleManager`
#   需在本类定义完成后才能解析. 本块所有符号仅被方法体引用(运行时), 类定义级别零依赖
#   (已用 AST 校验: 51 个符号无一用于基类/类属性/默认参数/装饰器/类型注解).
# 向后兼容: 模块完全加载后, 这些符号进入 globals(), 所有方法运行时正常访问.
from agent.digital_life import (
    _LIFETRACE_AVAILABLE, _PERSONA_AVAILABLE, _PLANNING_AVAILABLE,
    # ... 其余 48 个符号
)
```

同样的策略也应用于 [agent/orchestrator/orchestrator.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/orchestrator.py)。

### 两层防御的协同

| 层级 | 防御点 | 作用 |
|------|--------|------|
| 第一层 | `__init__.py` PEP 562 | 阻断 `import agent.orchestrator` 触发子模块加载 |
| 第二层 | 业务模块末尾导入 | 保证子模块加载时，被反向引用的符号已就绪 |

任一层独立成立即可解环；两层叠加提供冗余，避免后续单点改动重新引入循环。

## 四、验证方法

修复后执行以下三步验证：

```bash
# 1. 直接导入不报错
python -c "from agent.orchestrator import Orchestrator, LifecycleManager, TaskDispatcher; print('OK')"

# 2. 反向导入不报错
python -c "import agent.digital_life; print('OK')"

# 3. pytest 收集不报错（不实际运行，只收集）
python -m pytest tests/unit/ --co -q --timeout=60
```

三步均通过即认为循环依赖已解除。

## 五、预防措施

### 5.1 新增模块时的自检清单

- [ ] 新模块是否被 `agent/__init__.py` 顶层导入？若是，改用 PEP 562 懒加载。
- [ ] 新模块是否 `from agent.xxx import` 其他 `agent` 子包？若是，确认对方不会反向导入本模块。
- [ ] 若必须双向引用，将其中一方改为**函数内导入**或**文件末尾导入**。
- [ ] 提交前运行 `python -c "import agent.新模块"` 与 `pytest --co` 验证。

### 5.2 依赖注入（DI）模式

对于频繁双向依赖的核心组件，优先使用**工厂注入**而非直接导入。
[lifecycle_manager.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/lifecycle_manager.py) 文件头已采用此模式：

```python
class LifecycleManager:
    def __init__(self, *,
                 tool_calling_service_factory=None,
                 workflow_engine_factory=None,
                 subagent_manager_factory=None,
                 # ... 其余工厂参数
                 ):
        # 未注入时回落到延迟导入，保持 100% 向后兼容
        self._tool_calling = tool_calling_service_factory() if tool_calling_service_factory \
            else self._lazy_import_tool_calling()
```

工厂注入让**测试**可传入 mock，**生产**可延迟加载，从根本上避免加载时循环。

### 5.3 CI 守护

- `pytest.ini` 已配置 `--import-mode=importlib`，更严格地暴露循环导入。
- CI 矩阵覆盖 Windows + Python 3.12（循环导入在该组合最易暴露）。
- `-m "not slow"` 跳过慢测试，缩短反馈循环，让循环导入问题更快被发现。

## 六、常见误区

| 误区 | 纠正 |
|------|------|
| "本地能跑，CI 不能跑是 CI 的问题" | 循环导入对加载顺序敏感，pytest 收集顺序与本地 `python` 不同，更易暴露 |
| "加个 `try/except ImportError` 兜底就行" | 治标不治本，会掩盖真实循环，后续改动随时可能再炸 |
| "把所有导入都改成函数内导入" | 过度防御（违简易），增加调用开销与可读性成本；只在循环链上延迟 |
| "用 `importlib.import_module` 替代 `from ... import`" | 仅推迟问题，不改变循环结构；PEP 562 是更优雅的等价方案 |

## 七、参考

- [PEP 562 — Module `__getattr__` and `__dir__`](https://peps.python.org/pep-0562/)
- 修复涉及的文件：
  - [agent/orchestrator/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/__init__.py)（PEP 562 懒加载）
  - [agent/orchestrator/lifecycle_manager.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/lifecycle_manager.py)（末尾导入 + DI）
  - [agent/orchestrator/orchestrator.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/orchestrator.py)（末尾导入）
  - [agent/skills_mgmt/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/skills_mgmt/__init__.py)（同模式参考实现）
- 可复用扫描脚本：[scripts/check_circular_deps.py](file:///c:/Users/Administrator/agent/security-tools/scripts/check_circular_deps.py)

---

## 附录 A：全项目循环依赖风险扫描（2026-08-01）

> 用 [scripts/check_circular_deps.py](file:///c:/Users/Administrator/agent/security-tools/scripts/check_circular_deps.py) 对 `agent/` 全目录做 AST 扫描的结果。

### A.1 双向依赖检测：无即时风险 ✅

扫描所有顶层 `from agent.* import` 语句，**未发现** A↔B 双向顶层依赖。
已修复的 `orchestrator ↔ digital_life` 循环未复发，也未出现新的顶层循环。

### A.2 已使用 PEP 562 懒加载的模块 ✅

| 模块 | 状态 |
|------|------|
| [agent/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/__init__.py) | 已防御 |
| [agent/orchestrator/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/orchestrator/__init__.py) | 已防御 |
| [agent/skills_mgmt/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/skills_mgmt/__init__.py) | 已防御 |
| [agent/monitoring/__init__.py](file:///c:/Users/Administrator/agent/security-tools/agent/monitoring/__init__.py) | 已防御（2026-08-01 迁移） |

### A.3 潜在风险点：`__init__.py` 顶层导入重模块 ⚠️

以下 `__init__.py` 仍在**顶层**导入子模块（未使用 PEP 562）。
当前无循环，但若将来子模块反向引用包级符号，会立即形成循环：

| `__init__.py` | 顶层导入的子模块数 | 风险说明 |
|---------------|------------------|----------|
| `agent.cognitive.__init__` | 5 | actor_critic/reflection/loop/debate/knowledge |
| `agent.extensions.__init__` | 3 | base/manager/store，且 manager 又导入多个 installer |
| `agent.memory.__init__` | 3 | base/adapters/router |
| `agent.subagent.__init__` | 3 | lifecycle/sandbox/container |
| `agent.p6.__init__` | 3 | performance/snapshot/frequency |
| `agent.memory.adapters.__init__` | 2 | mem0_adapter/holographic_adapter |
| `agent.audit.__init__` | 1 | logger |
| `agent.network.__init__` | 1 | config_validator |

> `agent.monitoring.__init__`（原 7 个子模块，风险最高）已于 2026-08-01 迁移至 PEP 562，从本表移除。

**建议**：对上表中子模块数 ≥ 3 的包（cognitive、extensions、memory、subagent、p6），
在下次重构窗口评估是否迁移到 PEP 562 懒加载。优先级：`extensions` > `cognitive` > `memory`。

### A.4 已修复循环的运行时安全确认 ✅

`orchestrator` 与 `digital_life` 相关的 3 个文件，所有 `agent.*` 导入均位于**函数体内**（运行时延迟导入），加载时零依赖：

| 文件 | 函数内 agent 导入数 | 状态 |
|------|---------------------|------|
| `agent.orchestrator.lifecycle_manager` | 23 | 运行时安全 |
| `agent.orchestrator.orchestrator` | 8 | 运行时安全 |
| `agent.digital_life` | 1（→ orchestrator） | 运行时安全 |

### A.5 总体结论

| 维度 | 评级 | 说明 |
|------|------|------|
| 即时循环风险 | **低** | 无顶层双向依赖，已修复循环未复发 |
| 潜在风险面 | **中** | 8 个 `__init__.py` 仍用顶层导入，其中 5 个子模块数 ≥ 3 |
| 防御纵深 | **强** | 核心 4 个包已用 PEP 562，业务模块用函数内导入 + DI 工厂 |

**行动项**：
1. ~~将 `agent.monitoring.__init__` 迁移到 PEP 562~~ ✅ 已于 2026-08-01 完成。
2. ~~将 `check_circular_deps.py` 纳入 CI~~ ✅ 已接入 `code-quality` job 的"循环依赖守卫"步骤，发现新增顶层双向依赖时 `exit 1` 阻断流水线。
3. （后续）对 `extensions`/`cognitive`/`memory` 等子模块数 ≥ 3 的包，择机迁移 PEP 562。
4. 新增 `agent.*` 子包时，参考本文档第五节的自检清单。
