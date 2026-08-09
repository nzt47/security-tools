# rate_limiter 迁移技术方案对比分析

> 日期：2026-08-09 ｜ 状态：分析文档（供评审）
> 前置：[重构草稿](rate_limiter_registry_refactor_draft.md) ｜ [技术复盘](SingletonManager_Migration_Retrospective.md)
> 决策项：`rate_limiter` 是否收口到 SingletonManager、以及采用哪种方案

---

## 一、现状剖析

`rate_limiter` 采用**命名注册表**模式（[rate_limiter.py](../agent/rate_limiter.py) L560-600）：

```python
_global_limiters: dict[str, RateLimiter] = {}   # 按名缓存多实例
_global_limiters_lock = threading.Lock()
_default_limiter: Optional[RateLimiter] = None  # default 名缓存

def get_rate_limiter(name="default", **kwargs) -> RateLimiter:
    with _global_limiters_lock:
        # 已存在直接返回；不存在则 RateLimiter(**kwargs) 构造后缓存
```

**语义特征**：一名多实例注册表 + 动态 `**kwargs` 构造——与 SingletonManager"一名一实例 + 固定工厂"语义冲突。

**调用面（实测）**：
| 类型 | 位置 | 说明 |
|------|------|------|
| 生产调用 | `api_gateway.py:295` | `get_rate_limiter("api_gateway")`，唯一直接调用 |
| 包装 | `tool_calling.py:21` | 同签名函数内部委托，非直接调用 |
| 测试 | `test_rate_limiter_boundary.py` | 用 `get_rate_limiter("global_test_limiter")` / `register_rate_limiter` / `get_all_rate_limiter_status` |

---

## 二、方案 A：per-name 子单例（否决）

**思路**：每个命名 limiter 注册为一个 SingletonManager 单例（如 `rate_limiter.api_gateway`）。

```python
# 示意（伪码）：为每个名字动态注册
def get_rate_limiter(name="default", **kwargs):
    return get_singleton(f"rate_limiter.{name}", kwargs)
```

**致命问题**：

| # | 问题 | 影响 |
|---|------|------|
| A1 | **动态 `**kwargs` 与固定工厂冲突**：SingletonManager 工厂签名固定 `factory(config)`，无法表达"同名不同参数" | 同一名字二次调用带不同参数，语义不可控 |
| A2 | **名字不可枚举预注册**：注册名依赖运行时 name，需懒注册机制，SingletonManager 未设计该 API | 需侵入式改造管理器 |
| A3 | **测试污染**：测试中 `register_rate_limiter("global_test_limiter")` 会留下永久注册名 | 测试隔离成本高 |
| A4 | `get_all_rate_limiter_status`（遍历全部）无法基于单例 API 实现 | 需保留旧注册表，与方案目标矛盾 |
| A5 | 收益为零：没有场景需要"独立管理某个命名限流器" | 过度设计（违简易） |

**结论**：❌ 否决。改造管理器、语义破坏、收益为零，三重失败。

---

## 三、方案 B：注册表容器单例（推荐）

**思路**：把整个命名注册表封装为**一个容器对象**（`RateLimiterRegistry`），容器作为单一单例注册；4 个公共函数改为委托容器，签名与行为不变。

### 3.1 核心设计

```python
class RateLimiterRegistry:
    """命名注册表容器：一名多实例语义原样保留，容器本身被 SingletonManager 管理"""

    def __init__(self):
        self._limiters: Dict[str, RateLimiter] = {}
        self._default: Optional[RateLimiter] = None
        self._lock = threading.Lock()  # 锁粒度不变：容器内串行

    def get(self, name="default", **kwargs) -> RateLimiter:
        with self._lock:
            if name == "default" and self._default is not None:
                return self._default
            if name not in self._limiters:
                self._limiters[name] = RateLimiter(**kwargs)
            if name == "default":
                self._default = self._limiters[name]
            return self._limiters[name]

    def register(self, name, **kwargs) -> RateLimiter:
        with self._lock:
            limiter = RateLimiter(**kwargs)
            self._limiters[name] = limiter
            return limiter

    def status(self) -> dict:
        with self._lock:
            return {n: l.get_status() for n, l in self._limiters.items()}

    def reset(self) -> None:
        with self._lock:
            for l in self._limiters.values():
                l.reset()
            self._default = None
```

模块级改造（保留 fallback）：

```python
_registry: Optional[RateLimiterRegistry] = None

def _create_rate_limiter_registry(config=None):
    return RateLimiterRegistry()

def get_rate_limiter_registry() -> RateLimiterRegistry:
    if _SINGLETON_AVAILABLE:
        return get_singleton("rate_limiter_registry")
    global _registry
    if _registry is None:
        _registry = _create_rate_limiter_registry()
    return _registry

# 4 个公共函数委托容器（签名不变，调用方零改动）
def get_rate_limiter(name="default", **kwargs):
    return get_rate_limiter_registry().get(name, **kwargs)
def register_rate_limiter(name, **kwargs):
    return get_rate_limiter_registry().register(name, **kwargs)
def get_all_rate_limiter_status():
    return get_rate_limiter_registry().status()
def reset_global_limiters():
    get_rate_limiter_registry().reset()

def reset_rate_limiter_registry():   # 新增：销毁容器（测试隔离）
    global _registry
    if _SINGLETON_AVAILABLE:
        reset_singleton("rate_limiter_registry")
    _registry = None
```

### 3.2 兼容性与收益

| 维度 | 分析 |
|------|------|
| API 兼容 | 4 个公共函数签名/行为**完全不变**；唯一生产调用方 api_gateway 零改动 |
| 并发安全 | 锁从模块级移入容器，粒度相同（容器内串行），行为等价 |
| 测试兼容 | 既有 `test_rate_limiter_boundary.py` 全部用例原样通过（委托透明） |
| 测试隔离 | 新增 `reset_rate_limiter_registry()`，隔离入口统一 |
| 一致性收益 | 容器纳入 SingletonManager：全局 reset/统计可覆盖；与其余 50 个单例管理方式一致 |
| cleanup | 无需注册（限流器为纯计数器，无线程/外部资源） |

**差异点（需评审确认）**：`reset_global_limiters`（复位计数）与新增 `reset_rate_limiter_registry`（销毁容器）语义分离，避免测试误用。

---

## 四、方案 C：维持现状（暂缓）

**理由（原评估）**：命名注册表语义不匹配，强行迁移需特殊设计。

**补充实测**：生产调用面仅 1 处（api_gateway），`tool_calling.py` 仅为包装——**调用面小**意味着收口一致性收益也小。

**代价（维持现状的真实成本）**：
- 无法享受 SingletonManager 统一 reset/统计；
- 模块级锁与容器锁并存，管理方式两套；
- 后续新增命名限流器时，仍是"注册表模式"而非统一模式。

---

## 五、多维对比矩阵

| 维度 | 方案 A per-name | 方案 B 容器单例 | 方案 C 维持现状 |
|------|:---:|:---:|:---:|
| API 兼容 | ❌ 破坏 | ✅ 零改动 | ✅ 无改动 |
| 语义保真 | ❌ 冲突 | ✅ 原样保留 | ✅ 原样 |
| 测试隔离入口 | ❌ 无 | ✅ reset 函数 | ⚠️ 无统一 reset |
| 管理器改造 | ❌ 需侵入 | ✅ 不需 | — |
| 迁移成本 | 高 | 低（单文件单区域） | 零 |
| 一致性收益 | 低 | 中（统一管理） | 无 |
| 复杂度风险 | 高 | 低 | 无 |
| **综合** | **❌ 否决** | **✅ 推荐** | **⚠️ 可接受** |

---

## 六、推荐决策与实施

**推荐**：方案 B（注册表容器单例）。理由：成本低（单文件单区域）、API/语义/测试三重兼容、获得统一管理收益；无方案 A 的改造负担。

**决策条件（建议）**：
1. 若团队近期**计划扩展限流器使用**（新增命名场景）→ 收口方案 B，避免两套模式并存。
2. 若长期**仅 api_gateway 一处使用**且无扩展计划 → 维持方案 C 可接受（调用面小，收益有限）。

**实施步骤（若通过）**：
1. 按 [重构草稿](rate_limiter_registry_refactor_draft.md) 落地代码（容器类 + 委托 + fallback + 注册）。
2. 新增 `test_rate_limiter_registry_singleton.py`（单例/行为等价/并发/fallback）。
3. 回归：既有 `test_rate_limiter_boundary.py` + api_gateway 相关测试。
4. 同步更新：迁移清单（rate_limiter 勾选）、总结报告、wiki。

**回滚**：撤销容器类与委托，恢复原 L560-600 注册表区（改动集中于单文件，低风险）。

---

## 七、评审待决

- [ ] 是否收口（依据"扩展计划"判断）？
- [ ] 若收口，确认 `reset_rate_limiter_registry` 命名与语义分离设计。
- [ ] 确认容器不注册 cleanup 钩子（纯计数器无资源生命周期）。
