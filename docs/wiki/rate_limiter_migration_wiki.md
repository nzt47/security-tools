# rate_limiter 暂缓方案 Wiki

> 归档日期：2026-08-09 ｜ 来源：[方案对比分析](../rate_limiter_refactor_analysis.md) ｜ [重构草稿](../rate_limiter_registry_refactor_draft.md)
> 主页面：[SingletonManager 统一单例管理 Wiki](singleton_manager_wiki.md)

---

## 背景

`rate_limiter` 使用**命名注册表**模式（`_global_limiters: dict[str, RateLimiter]` + `_default_limiter` 缓存），与 SingletonManager **单实例语义**不匹配，故在统一单例迁移中**维持暂缓**。本页归档备选方案供后续评估。

## 现状要点（实测）

- 生产调用面仅 1 处：`api_gateway.py:295` `get_rate_limiter("api_gateway")`
- `tool_calling.py:21` 仅为同签名包装函数
- 测试：`test_rate_limiter_boundary.py` 使用 `get_rate_limiter` / `register_rate_limiter` / `get_all_rate_limiter_status`

## 方案对比（三选一）

| 方案 | 思路 | 结论 |
|------|------|------|
| A. per-name 子单例 | 每个命名 limiter 注册为一个单例 | ❌ 否决：动态 `**kwargs` 与固定工厂冲突、需侵入管理器、测试污染 |
| B. 注册表容器单例 | 把整个命名注册表封装为**一个容器对象**注册，4 个公共函数委托容器 | ✅ 推荐：API/语义/测试三重兼容，低迁移成本 |
| C. 维持现状 | 保留注册表模式 | ⚠️ 可接受：调用面小，但两套管理方式并存 |

## 决策条件

1. 若团队**计划扩展限流器使用**（新增命名场景）→ 收口方案 B，避免两套模式并存。
2. 若长期**仅 api_gateway 一处使用**且无扩展计划 → 维持方案 C 可接受。

## 方案 B 核心思路（代码要点）

```python
class RateLimiterRegistry:
    """命名注册表容器：一名多实例语义原样保留，容器本身被 SingletonManager 管理"""
    def __init__(self):
        self._limiters = {}            # dict[str, RateLimiter]
        self._default = None
        self._lock = threading.Lock()  # 锁粒度不变

    def get(self, name="default", **kwargs): ...   # 语义与原 get_rate_limiter 一致
    def register(self, name, **kwargs): ...         # 语义与原 register_rate_limiter 一致
    def status(self): ...                            # 语义与原 get_all_rate_limiter_status 一致
    def reset(self): ...                             # 语义与原 reset_global_limiters 一致
```

- 注册单例名：`rate_limiter_registry`（无 cleanup——限流器为纯计数器，无资源生命周期）
- 新增 `reset_rate_limiter_registry()` 供测试隔离
- 完整代码见 [重构草稿](../rate_limiter_registry_refactor_draft.md)

## 风险与回滚

- 风险低：改动集中于单文件单区域，无外部资源/后台线程
- 回滚：撤销容器类与委托，恢复原注册表区即可

## 决策记录

| 日期 | 决策 | 说明 |
|------|------|------|
| 2026-08-09 | 维持暂缓 | 命名注册表语义不匹配，暂缓理由登记 |
| 待评审 | 按扩展计划二选一 | 详见"决策条件" |
