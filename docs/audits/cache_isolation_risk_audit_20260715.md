# 缓存隔离风险审计报告

**审计日期**: 2026-07-15
**审计范围**: `agent/` 目录下所有涉及内存缓存或持久化的模块
**审计方法**: 代码静态分析 + 模式匹配（`return self._cache` / `self._cache = data` / 浅拷贝）
**关联文档**: [manual_vs_pickle_comparison_20260715.md](file:///c:/Users/Administrator/agent/docs/audits/manual_vs_pickle_comparison_20260715.md)

---

## 1. 审计概要

### 1.1 已修复模块（6 个）

| 模块 | 方案 | 状态 |
|------|------|------|
| [extensions/store.py](file:///c:/Users/Administrator/agent/agent/extensions/store.py) | manual rebuild | ✅ 已修复 |
| [workflow_learning/repository.py](file:///c:/Users/Administrator/agent/agent/workflow_learning/repository.py) | pickle roundtrip | ✅ 已修复 |
| [extensions/market.py](file:///c:/Users/Administrator/agent/agent/extensions/market.py) | deepcopy | ✅ 已修复 |
| [skills_mgmt/store.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/store.py) | deepcopy | ✅ 已修复 |
| [network/config_manager.py](file:///c:/Users/Administrator/agent/agent/network/config_manager.py) | _load + _load_safe | ✅ 已修复 |
| [system_prompt_config.py](file:///c:/Users/Administrator/agent/agent/system_prompt_config.py) | deepcopy | ✅ 已修复 |

### 1.2 本次审计发现的风险点

| 模块 | 风险等级 | 违反契约 | 状态 |
|------|---------|---------|------|
| [graceful_degrade.py](file:///c:/Users/Administrator/agent/agent/graceful_degrade.py) L272 | 中 | 出口隔离 | 待评估 |
| [memory_optimized.py](file:///c:/Users/Administrator/agent/agent/memory_optimized.py) L151 | 低 | 出口隔离 | 待评估 |
| [digital_life_persona.py](file:///c:/Users/Administrator/agent/agent/digital_life_persona.py) L328, L409 | 无 | str 不可变 | 无需修复 |
| [data_analytics.py](file:///c:/Users/Administrator/agent/agent/data_analytics.py) | 无 | 无对外返回缓存 | 无需修复 |
| [llm_response_cache.py](file:///c:/Users/Administrator/agent/agent/llm_response_cache.py) | 无 | MultiLevelCache 内部处理 | 无需修复 |

---

## 2. 风险点详细分析

### 2.1 graceful_degrade.py — 中等风险

**位置**: [agent/graceful_degrade.py L263-L272](file:///c:/Users/Administrator/agent/agent/graceful_degrade.py#L263-L272)

**代码**:
```python
def _cache_get(self, key: str) -> Optional[Any]:
    """从缓存获取数据（检查 TTL）"""
    with self._lock:
        if key not in self._cache:
            return None
        expiry, data = self._cache[key]
        if time.time() > expiry:
            del self._cache[key]
            return None
        return data  # ← 返回直接引用
```

**违反契约**: 出口隔离 — `return data` 返回缓存中 `data` 的直接引用

**风险分析**:
- TTL 缓存，存储计算结果（如降级指标、组件状态）
- 调用方（如 `get_component_status` L456-458、`get_dashboard` L625-627）获取后可能修改返回值
- 若修改的是 dict/list，会污染缓存中的 `data`
- 但 TTL 到期后缓存自动失效，污染窗口有限

**影响范围**:
- L456: `cached = self._cache_get(component); if cached: return cached`
- L492, L510, L548: 类似模式
- L625: `cached = self._cache_get("dashboard"); if cached: return cached`

**建议修复方案**:
```python
from copy import deepcopy

def _cache_get(self, key: str) -> Optional[Any]:
    with self._lock:
        if key not in self._cache:
            return None
        expiry, data = self._cache[key]
        if time.time() > expiry:
            del self._cache[key]
            return None
        return deepcopy(data)  # ← 出口隔离
```

**优先级**: 中 — 降级模块在高负载时被频繁调用，缓存污染可能导致降级决策基于被篡改的数据。

---

### 2.2 memory_optimized.py — 低风险

**位置**: [agent/memory_optimized.py L143-L153](file:///c:/Users/Administrator/agent/agent/memory_optimized.py#L143-L153)

**代码**:
```python
def get(self, persist_directory: str, collection_name: str) -> Optional[dict]:
    """获取缓存的初始化参数"""
    key = self._make_key(persist_directory, collection_name)
    with self._lock:
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]['config']  # ← 返回直接引用
    return None
```

**违反契约**: 出口隔离 — 返回 `self.cache[key]['config']` 直接引用

**风险分析**:
- ChromaDB 初始化配置缓存
- 调用方 [L254-L258](file:///c:/Users/Administrator/agent/agent/memory_optimized.py#L254-L258): `cached_config = self._cache.get(...); if cached_config: self._apply_cached_config(cached_config); return`
- `_apply_cached_config` [L266-L270](file:///c:/Users/Administrator/agent/agent/memory_optimized.py#L266-L270) 只设置 `self._initialized = True`，**不修改 config**
- 实际污染风险极低，因为调用方只读取不写入

**建议修复方案**:
```python
from copy import deepcopy

def get(self, persist_directory: str, collection_name: str) -> Optional[dict]:
    key = self._make_key(persist_directory, collection_name)
    with self._lock:
        if key in self.cache:
            self.cache.move_to_end(key)
            return deepcopy(self.cache[key]['config'])  # ← 出口隔离
    return None
```

**优先级**: 低 — 当前调用方不修改返回值，但未来调用方变更可能引入污染。建议预防性修复。

---

### 2.3 digital_life_persona.py — 无风险

**位置**: [agent/digital_life_persona.py L325-L328, L406-L409](file:///c:/Users/Administrator/agent/agent/digital_life_persona.py#L325-L328)

**代码**:
```python
def _build_tool_status_text(self) -> str:
    if self._cached_tool_status is not None:
        return self._cached_tool_status  # ← 返回直接引用
    # ...
    self._cached_tool_status = result
    return result
```

**分析**:
- 缓存的是 `str` 类型（不可变）
- 调用方无法修改 str 内容，`str` 操作总是返回新对象
- **无污染风险**，无需修复

---

### 2.4 data_analytics.py — 无风险

**位置**: [agent/data_analytics.py L59-L60](file:///c:/Users/Administrator/agent/agent/data_analytics.py#L59-L60)

**代码**:
```python
self._cache = {}
self._cache_ttl = 300  # 秒
```

**分析**:
- 仅有 `self._cache = {}` 定义，无 `_load()` / `_persist()` 方法
- 无对外返回 `self._cache` 的方法
- **无风险**，无需修复

---

### 2.5 llm_response_cache.py — 无风险

**位置**: [agent/llm_response_cache.py L78, L147](file:///c:/Users/Administrator/agent/agent/llm_response_cache.py#L78-L147)

**代码**:
```python
self._cache = MultiLevelCache(l1_max_size=max_size, l1_ttl=ttl_seconds, l2_enabled=False)
# ...
result = self._cache.get(prompt_hash)  # ← MultiLevelCache.get() 返回
```

**分析**:
- `self._cache` 是 `MultiLevelCache` 实例，不是直接 dict
- `MultiLevelCache.get()` 内部处理隔离（返回值的 deepcopy 或独立副本）
- **无风险**，无需修复

---

## 3. 已修复模块验证

### 3.1 extensions/store.py (manual rebuild)

**验证点**:
- ✅ `_load()` 返回 `_rebuild_cache(self._cache)` — 出口隔离
- ✅ `_save(data)` 末尾 `self._cache = _rebuild_cache(data)` — 入口同步
- ✅ `add()`/`remove()`/`update_status()` 调用 `_save(data)` 而非直接 `self._cache = data`
- ✅ 9 个隔离测试全部通过

### 3.2 workflow_learning/repository.py (pickle roundtrip)

**验证点**:
- ✅ `_load()` 返回 `_isolate_cache(self._cache)` — 出口隔离
- ✅ `_persist(data)` 接收 data 参数，末尾 `self._cache = _isolate_cache(data)` — 入口同步
- ✅ `upsert()`/`remove()` 传 `_persist(data)` 而非无参 `_persist()`
- ✅ 9 个隔离测试全部通过

### 3.3 其他已修复模块

| 模块 | 验证状态 |
|------|---------|
| extensions/market.py | ✅ `get_cached_community_index()` 返回 deepcopy |
| skills_mgmt/store.py | ✅ 全量 deepcopy 模式 |
| network/config_manager.py | ✅ `_load` 内部直引 + `_load_safe` 对外隔离 |
| system_prompt_config.py | ✅ `load()` 返回 deepcopy + `save()` 同步缓存 |

---

## 4. 建议修复优先级

| 优先级 | 模块 | 风险 | 建议方案 | 预估工作量 |
|--------|------|------|---------|-----------|
| P2 | graceful_degrade.py | 中 | `_cache_get` 返回 `deepcopy(data)` | 1 行代码 + 测试 |
| P3 | memory_optimized.py | 低 | `get` 返回 `deepcopy(config)` | 1 行代码 + 测试 |

**说明**:
- P2 (graceful_degrade): 降级模块在高负载时频繁调用，缓存数据是降级决策依据，建议尽快修复
- P3 (memory_optimized): 当前调用方不修改返回值，但建议预防性修复以防未来变更

---

## 5. 自检清单

新增或修改缓存模块时，逐项检查：

| # | 检查项 | 验证方式 | 状态 |
|---|--------|---------|------|
| 1 | `_load()`/`get_*()` 返回独立副本 | grep `return self._cache` 应为 0 | ✅ 已修复模块通过 |
| 2 | `_persist(data)`/`_save(data)` 接收 data 参数 | 检查方法签名 | ✅ 已修复模块通过 |
| 3 | 方法结尾有 `self._cache = deepcopy/manual/pickle(data)` | grep 验证 | ✅ 已修复模块通过 |
| 4 | `upsert/remove` 传 `_persist(data)` | 检查调用方 | ✅ 已修复模块通过 |
| 5 | 返回模块级常量时做 deepcopy | 检查 BUILTIN_* 常量 | ✅ market.py 已修复 |
| 6 | 持锁期间无文件 I/O | 检查锁内代码块 | ✅ 已修复模块通过 |
| 7 | 有 `*_not_shared` / `*_isolation` 测试 | 检查测试文件 | ✅ 18 个隔离测试已添加 |

---

## 6. 总结

本次审计共检查 11 个模块：
- **6 个已修复** — 全部通过契约验证
- **2 个有风险** — graceful_degrade (中) 和 memory_optimized (低)，建议按优先级修复
- **3 个无风险** — digital_life_persona (str 不可变)、data_analytics (无对外返回)、llm_response_cache (MultiLevelCache 内部处理)

项目整体缓存隔离状态良好，核心高频路径（store.py、repository.py）已采用性能优化的隔离方案，剩余风险点影响范围有限。
