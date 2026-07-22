# 缓存隔离方案对比：manual rebuild vs pickle roundtrip

**文档日期**: 2026-07-15
**适用范围**: 所有涉及内存缓存 + 文件持久化的高频调用模块
**关联模块**: [extensions/store.py](file:///c:/Users/Administrator/agent/agent/extensions/store.py) (manual rebuild)、[workflow_learning/repository.py](file:///c:/Users/Administrator/agent/agent/workflow_learning/repository.py) (pickle roundtrip)
**基准测试脚本**: [_cache_isolation_benchmark.py](file:///c:/Users/Administrator/agent/docs/audits/_cache_isolation_benchmark.py)

---

## 1. 背景

缓存隔离是「内存缓存 + 文件持久化」模块的核心安全契约。直接返回 `self._cache` 引用会导致调用方修改返回值时污染缓存，引发难以排查的数据不一致问题。

传统方案使用 `copy.deepcopy`，但其在高频调用场景下性能开销显著。本文档对比两种优化方案——**manual rebuild** 和 **pickle roundtrip**——并提供选型决策框架。

---

## 2. 方案概述

### 2.1 copy.deepcopy（基准）

通用性最强的深拷贝方案，递归复制所有对象。

```python
from copy import deepcopy

def _load(self):
    return deepcopy(self._cache)
```

- **优点**: 通用，支持任意对象（含自定义类、循环引用）
- **缺点**: 性能最差，维护 memo dict 有额外开销

### 2.2 pickle roundtrip

通过 `pickle.loads(pickle.dumps(data))` 实现深拷贝。

```python
import pickle

def _isolate_cache(data):
    return pickle.loads(pickle.dumps(data))

def _load(self):
    return _isolate_cache(self._cache)
```

- **优点**: 比 deepcopy 快 2-5 倍，C 实现序列化效率高
- **缺点**: 要求数据可 pickle（无 lambda、无嵌套自定义对象）
- **限制**: 仅限 Python 进程内使用，不可跨语言传输

### 2.3 manual rebuild

手动递归复制 dict/list，对不可变类型直接返回。

```python
def _rebuild_value(v):
    if isinstance(v, dict):
        return {k: _rebuild_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_rebuild_value(item) for item in v]
    return v  # str/int/float/bool/None 直接返回

def _rebuild_cache(data):
    return {key: [_rebuild_value(item) for item in items]
            for key, items in data.items()}
```

- **优点**: 比 deepcopy 快 3-12 倍（取决于数据结构），无类型分派开销
- **缺点**: 要求数据结构已知（仅 dict/list/str/int/float/bool/None）
- **限制**: 不支持 set/tuple/自定义对象；数据必须来自 `json.load()`

---

## 3. 实测性能数据

**测试环境**: Windows 10, Python 3.12.0, 1000 次迭代平均（500 条时 100 次）

### 3.1 extensions.json 结构（store.py 场景）

数据特征: `{key: [{ext_id, name, config: {nested: {...}}, tags: [...], ...}, ...]}`

| 规模 | deepcopy | json roundtrip | pickle roundtrip | manual rebuild | shallow copy | manual/deepcopy |
|------|----------|---------------|-----------------|---------------|-------------|----------------|
| 1 | 0.018ms | 0.013ms | 0.006ms | 0.005ms | 0.001ms | 3.8x |
| 10 | 0.130ms | 0.077ms | 0.028ms | 0.044ms | 0.001ms | 3.0x |
| 100 | 1.306ms | 0.726ms | 0.279ms | 0.489ms | 0.001ms | 2.7x |
| 500 | 6.685ms | 3.899ms | 1.504ms | 2.330ms | 0.003ms | 2.9x |

### 3.2 learned_workflows.json 结构（repository.py 场景）

数据特征: `{wf_id: {id, name, steps: [{params_template: {nested: {...}}, ...}], tags: [...], ...}}`

| 规模 | deepcopy | json roundtrip | pickle roundtrip | manual rebuild | shallow copy | manual/deepcopy |
|------|----------|---------------|-----------------|---------------|-------------|----------------|
| 1 | 0.023ms | 0.019ms | 0.007ms | 0.002ms | 0.000ms | 10.4x |
| 10 | 0.218ms | 0.142ms | 0.050ms | 0.020ms | 0.002ms | 10.7x |
| 100 | 2.261ms | 1.348ms | 0.571ms | 0.195ms | 0.009ms | 11.6x |
| 500 | 11.791ms | 7.745ms | 3.365ms | 0.982ms | 0.048ms | 12.0x |

### 3.3 性能差异分析

**为什么 manual rebuild 在两种数据结构上加速比差异大（2.7x vs 11.6x）？**

| 因素 | extensions.json | learned_workflows.json |
|------|----------------|----------------------|
| 顶层结构 | `{key: List[Dict]}` 5 个键 | `{wf_id: Dict}` N 个键 |
| 每条记录字段数 | 8 个（含嵌套 config） | 12 个（含嵌套 steps） |
| 嵌套深度 | 3 层（config.nested） | 4 层（steps[].params_template） |
| manual rebuild 优势 | dict comprehension 快 | 顶层 dict 直接遍历，省去 deepcopy 的类型分派 |

**关键发现**: pickle roundtrip 在 extensions.json 结构上（0.279ms）比 manual rebuild（0.489ms）更快！原因是 pickle 的 C 实现在处理浅层嵌套 dict 时效率很高，而 manual rebuild 的 Python 层递归有函数调用开销。但在深层嵌套的 workflows 结构上，manual rebuild 的优势充分发挥（0.195ms vs 0.571ms）。

### 3.4 隔离性验证

| 方案 | 顶层 dict 隔离 | 嵌套 list 隔离 | 嵌套 dict 隔离 | 结论 |
|------|-------------|-------------|-------------|------|
| deepcopy | ✓ | ✓ | ✓ | 完全隔离 |
| json roundtrip | ✓ | ✓ | ✓ | 完全隔离 |
| pickle roundtrip | ✓ | ✓ | ✓ | 完全隔离 |
| manual rebuild | ✓ | ✓ | ✓ | 完全隔离 |
| shallow copy | ✓ | ✗ | ✗ | 浅隔离（不可用） |

---

## 4. 选型决策框架

### 4.1 决策树

```
数据是否来自 json.load()？
├─ 是 → 数据结构是否已知且稳定？
│   ├─ 是 → 数据规模是否 > 100 条？
│   │   ├─ 是 → manual rebuild（性能最优）
│   │   └─ 否 → deepcopy（通用性优先）
│   └─ 否 → deepcopy（避免结构变更导致隔离失败）
└─ 否 → 数据是否可 pickle？
    ├─ 是 → pickle roundtrip（性能与通用性平衡）
    └─ 否 → deepcopy（唯一选择）
```

### 4.2 方案对比矩阵

| 维度 | deepcopy | pickle roundtrip | manual rebuild |
|------|----------|-----------------|---------------|
| **性能** (100条 workflows) | 2.261ms | 0.571ms | 0.195ms |
| **加速比** | 1.0x | 4.0x | 11.6x |
| **隔离性** | 完全 | 完全 | 完全 |
| **通用性** | 任意对象 | 可 pickle 对象 | dict/list/基础类型 |
| **数据来源要求** | 无 | 无 | json.load() |
| **维护成本** | 低 | 低 | 中（结构变更需同步） |
| **跨进程安全** | 否 | 是（可传输） | 否 |
| **依赖** | 标准库 | 标准库 | 无 |

### 4.3 适用场景

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| 高频调用 + JSON 数据 + 结构稳定 | manual rebuild | 性能最优，store.py 采用 |
| 高频调用 + 可 pickle 数据 + 结构复杂 | pickle roundtrip | 性能与通用性平衡，repository.py 采用 |
| 低频调用 + 任意数据 | deepcopy | 通用性优先，market.py 采用 |
| 需要跨进程传输 | pickle roundtrip | 序列化天然支持 |
| 数据结构频繁变更 | deepcopy | 避免手动维护 rebuild 逻辑 |

---

## 5. 实施案例

### 5.1 extensions/store.py — manual rebuild

**选型理由**: 扩展数据来自 `json.load()`，结构稳定（5 个固定键），高频调用（每次扩展 CRUD 都触发）。

**核心代码**:
```python
def _rebuild_value(v: Any) -> Any:
    """递归复制可变值（manual rebuild 核心函数）"""
    if isinstance(v, dict):
        return {k: _rebuild_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_rebuild_value(item) for item in v]
    return v

def _rebuild_cache(data: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    return {key: [_rebuild_value(item) for item in items]
            for key, items in data.items()}
```

**应用点**:
- `_load()` 返回 `_rebuild_cache(self._cache)` — 出口隔离
- `_save(data)` 末尾 `self._cache = _rebuild_cache(data)` — 入口同步

### 5.2 workflow_learning/repository.py — pickle roundtrip

**选型理由**: 工作流数据结构较复杂（嵌套 steps + params_template），但可 pickle；`LearnedWorkflow.model_dump()` 返回纯 dict，满足 pickle 要求。

**核心代码**:
```python
import pickle

def _isolate_cache(data: Dict[str, dict]) -> Dict[str, dict]:
    """用 pickle roundtrip 隔离缓存数据"""
    return pickle.loads(pickle.dumps(data))
```

**应用点**:
- `_load()` 返回 `_isolate_cache(self._cache)` — 出口隔离
- `_persist(data)` 末尾 `self._cache = _isolate_cache(data)` — 入口同步

### 5.3 extensions/market.py — deepcopy（对照）

**选型理由**: 常量数据量小（BUILTIN_EXTENSIONS 固定），调用频率低，通用性优先。

```python
from copy import deepcopy

def get_cached_community_index(self):
    if self._cache:
        return deepcopy(self._cache)
    # ...
```

---

## 6. 注意事项与最佳实践

### 6.1 manual rebuild 的安全前提

1. **数据来源必须是 `json.load()`**: 确保类型限定为 dict/list/str/int/float/bool/None
2. **结构变更需同步**: 如果 JSON schema 新增了 set/tuple 类型字段，`_rebuild_value` 需要扩展
3. **添加防御性断言**（可选）: 在调试模式下可断言数据类型

```python
def _rebuild_value(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _rebuild_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_rebuild_value(item) for item in v]
    # 不可变类型直接返回
    assert v is None or isinstance(v, (str, int, float, bool)), f"未预期类型: {type(v)}"
    return v
```

### 6.2 pickle roundtrip 的安全前提

1. **数据可 pickle**: 无 lambda 函数、无嵌套自定义对象、无 socket/file handle
2. **不要 pickle 不可信数据**: pickle 反序列化有安全风险（代码执行），仅用于内部缓存隔离
3. **Pydantic model_dump() 安全**: `model_dump()` 返回纯 dict，天然可 pickle

### 6.3 通用最佳实践

1. **出口隔离**: `_load()` / `get_*()` / `list_*()` 必须返回独立副本
2. **入口同步**: `_save(data)` / `_persist(data)` 必须接收 data 参数并同步 `self._cache`
3. **调用方传参**: `upsert()` / `remove()` 必须传 `_persist(data)` 而非无参 `_persist()`
4. **测试守护**: 每个模块必须有 `*_not_shared` 和 `*_isolation` 测试

---

## 7. 总结

| 方案 | 性能 | 通用性 | 维护成本 | 推荐场景 |
|------|------|--------|---------|---------|
| manual rebuild | ★★★★★ (11.6x) | ★★ | ★★★ | JSON 数据 + 结构稳定 + 高频 |
| pickle roundtrip | ★★★★ (4.0x) | ★★★ | ★★★★★ | 可 pickle 数据 + 高频 |
| deepcopy | ★★ (1.0x) | ★★★★★ | ★★★★★ | 任意数据 + 低频 |

**核心原则**: 在保证隔离性的前提下，按数据特征选择性能最优的方案。不要过度优化低频路径，也不要在高频路径上使用 deepcopy。
