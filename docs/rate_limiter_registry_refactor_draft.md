# rate_limiter 注册表容器单例化重构草稿

> 状态：**草案（DRAFT）—— 仅供评审，未应用到项目代码**
> 关联：[迁移总结报告](SingletonManager_Migration_Summary_Report.md) 第七节 ｜ [优先级评估](SingletonManager_Migration_Priority_Report.md)
> 定位：`rate_limiter` 维持暂缓的备选方案。若团队决定全量收口，按下述方案评审后实施。

---

## 一、问题与方案对比

`rate_limiter` 使用**命名注册表**（`_global_limiters: dict[str, RateLimiter]` + `_default_limiter` 缓存），与 SingletonManager **单实例语义**不匹配。

| 方案 | 描述 | 成本 | 结论 |
|------|------|------|------|
| A. per-name 子单例 | 每个命名 limiter 注册为一个单例 | 高：与动态 `**kwargs` 构造冲突，需每次 get 检查配置差异 | ❌ 否决 |
| B. 注册表容器单例 | 把整个命名注册表（含锁与 default 缓存）封装为**一个容器对象**注册 | 低：4 个公共函数签名不变，语义不变 | ✅ **采用** |

方案 B 思路：SingletonManager 单例化的对象是"注册表容器"，而非每个限流器；容器内部保留现有按名缓存逻辑。

---

## 二、核心代码草稿

### 2.1 新增容器类（rate_limiter.py 顶部，`RateLimiter` 定义之后）

```python
class RateLimiterRegistry:
    """限流器命名注册表容器（整体单例化）

    保持现有"按名缓存多实例 + default 缓存"语义，
    将锁与容器状态封装为单一对象，供 SingletonManager 统一管理。
    """

    def __init__(self):
        self._limiters: Dict[str, RateLimiter] = {}
        self._default: Optional[RateLimiter] = None
        self._lock = threading.Lock()

    def get(self, name: str = "default", **kwargs) -> RateLimiter:
        """获取（或创建）命名限流器，语义与原 get_rate_limiter 一致"""
        with self._lock:
            if name == "default" and self._default is not None:
                return self._default
            if name not in self._limiters:
                self._limiters[name] = RateLimiter(**kwargs)
            if name == "default":
                self._default = self._limiters[name]
            return self._limiters[name]

    def register(self, name: str, **kwargs) -> RateLimiter:
        """注册限流器（覆盖同名），语义与原 register_rate_limiter 一致"""
        with self._lock:
            limiter = RateLimiter(**kwargs)
            self._limiters[name] = limiter
            return limiter

    def status(self) -> dict:
        """所有限流器状态，语义与原 get_all_rate_limiter_status 一致"""
        with self._lock:
            return {n: l.get_status() for n, l in self._limiters.items()}

    def reset(self) -> None:
        """复位所有限流器计数，语义与原 reset_global_limiters 一致"""
        with self._lock:
            for limiter in self._limiters.values():
                limiter.reset()
            self._default = None
```

### 2.2 模块级改造（替换原 L560-600 注册表区）

```python
# ── 全局限流器注册表（SingletonManager 统一收口） ──────────────

_registry: Optional[RateLimiterRegistry] = None  # fallback 变量向后兼容


def _create_rate_limiter_registry(config=None):
    """RateLimiterRegistry 工厂（供 SingletonManager 使用）"""
    return RateLimiterRegistry()


def get_rate_limiter_registry() -> RateLimiterRegistry:
    """获取全局限流器注册表单例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("rate_limiter_registry")
    global _registry
    if _registry is None:
        _registry = _create_rate_limiter_registry()
    return _registry


# 现有 4 个公共函数改为委托容器（签名与行为完全不变，调用方零改动）
def get_rate_limiter(name: str = "default", **kwargs) -> RateLimiter:
    return get_rate_limiter_registry().get(name, **kwargs)


def register_rate_limiter(name: str, **kwargs) -> RateLimiter:
    return get_rate_limiter_registry().register(name, **kwargs)


def get_all_rate_limiter_status() -> dict:
    return get_rate_limiter_registry().status()


def reset_global_limiters() -> None:
    """复位所有限流器计数（不销毁注册表）"""
    get_rate_limiter_registry().reset()


def reset_rate_limiter_registry():
    """销毁注册表单例（仅测试隔离用，reset 会丢弃全部命名限流器）"""
    global _registry
    if _SINGLETON_AVAILABLE:
        reset_singleton("rate_limiter_registry")
    _registry = None
```

### 2.3 文件末尾注册（置于 `_safe_call` 之后）

```python
# 注册单例工厂：注册表容器无线程/外部资源，不注册 cleanup 钩子
if _SINGLETON_AVAILABLE:
    register_singleton("rate_limiter_registry", _create_rate_limiter_registry)
```

---

## 三、兼容性分析

| 关注点 | 结论 |
|--------|------|
| 公共 API | `get_rate_limiter` / `register_rate_limiter` / `get_all_rate_limiter_status` / `reset_global_limiters` 签名与行为不变 |
| 并发安全 | 锁从模块级 `_global_limiters_lock` 移入容器 `self._lock`，粒度不变（同一容器内串行） |
| 现有测试 | 仅新增注册表单例，不改变限流语义；`test_rate_limiter` 既有用例应全量通过 |
| 生产调用方 | `get_rate_limiter("api_gateway")` 等无需改动 |
| 与 SingletonManager 语义 | 单例化对象是容器（一名一实例），不再与命名注册表冲突 ✅ |

**差异点（需评审确认）**：原 `reset_global_limiters` 复位计数但保留注册表结构；迁移后行为不变。新增 `reset_rate_limiter_registry` 才销毁结构（供测试隔离）。

---

## 四、测试计划

1. **单例**：唯一性、注册名 `rate_limiter_registry`、reset/GC/幂等。
2. **行为等价**：`get_rate_limiter("api_gateway")` 两次同实例；default 缓存；register 覆盖。
3. **并发**：多线程并发首建容器一次（双检锁）；并发 get 同名 limiter 同实例。
4. **fallback**：SingletonManager 不可用时行为一致。
5. **回归**：既有 `test_rate_limiter` + 相关集成全量通过。

---

## 五、风险与回滚

- **风险**：低。仅模块级状态归集到容器对象，无外部资源、无后台线程、无 config 通道需求。
- **回滚**：撤销注册与委托，恢复原 L560-600 注册表区即可（改动集中于单文件单区域）。

---

## 六、评审待决

- [ ] 是否将本草案升级为实施计划（用户决策）
- [ ] 确认新增 `reset_rate_limiter_registry` 命名（或沿用 `reset_global_limiters` 语义扩展）
- [ ] 确认不注册 cleanup 钩子（限流器为纯计数器，无资源生命周期）
