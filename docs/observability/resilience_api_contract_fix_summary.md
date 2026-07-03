# 弹性模块 API 契约修复技术总结

> 日期：2026-07-04
> 范围：circuit_breaker.py、rate_limiter.py、graceful_degrade.py、disaster_recovery.py
> 提交：`efad4bc5`
> 测试：180 passed（边界 + 场景）

## 1. 背景与问题

四个弹性模块在前期重构中，测试文件按新 API 期望编写，但主代码实现停留在旧 API，导致大量测试失败。核心矛盾：
- 构造函数签名不匹配（测试传 Config 对象，主代码只接受位置参数）
- 方法名/方法签名不匹配（测试调用 `check(endpoint=, user_id=)`，主代码只有 `check(tool_name)`）
- 枚举值缺失（测试使用 `LENIENT/CACHE_ONLY/SKIP/EMERGENCY`，主代码只有 `NORMAL/RETRY/RELAXED`）
- 返回值结构不匹配（测试解构元组，主代码返回标量）

## 2. 核心逻辑调整点

### 2.1 circuit_breaker.py — 多态构造 + 字段重命名兼容

**调整点：**
1. **CircuitBreakerConfig 数据类**：新增 `failure_threshold/min_requests/reset_timeout/window_seconds/max_attempts/name` 字段，作为构造参数的载体
2. **三态构造**：`CircuitBreaker(config)` / `CircuitBreaker(failure_threshold=, reset_timeout=)` / `CircuitBreaker(name)` 三种形式共存
3. **CircuitStats 字段重命名 + 别名兼容**：
   - `total_calls → total_requests`
   - `success_count → successes`
   - `failure_count → failures`
   - 通过 `@property` 别名保持旧字段可读，避免破坏既有调用方
4. **protect/protect_async 上下文管理器**：替代旧的 `call` 方法，支持 `with cb.protect():` 和 `async with cb.protect_async():`
5. **CircuitBreakerManager + 全局注册函数**：`register_circuit_breaker/get_circuit_breaker/get_all_circuit_breaker_status`

**设计决策：** 字段重命名用 `@property` 别名而非直接改名，因为 `api_gateway.py` 等主代码仍读取旧字段名。这是兼容成本最低的方案。

### 2.2 rate_limiter.py — 多级限流 + 用户隔离 + 死锁修复

**调整点：**
1. **TokenBucket 原语**：独立类，支持 `try_acquire/release/reset/get_wait_time/to_dict`，线程安全
2. **多态构造**：`RateLimiter(limits_dict)` 走旧 API，`RateLimiter(max_concurrent=, strategy=)` 走新 API
3. **多级限流链**：全局 → 接口 → 用户 → 并发，任一级失败时按优先级回退已消费的令牌
4. **用户限流独立检查**（关键修复）：
   - 原实现将用户检查嵌套在接口检查内部，导致 `check(user_id=)` 无 endpoint 时跳过用户限流
   - 修复后用户检查独立于接口检查，`check(user_id="u1")` 即使无 endpoint 也会执行用户级限流
5. **用户桶规则查找**（关键修复）：
   - 原实现用户桶 key = `f"user/{user_id}"`，但规则注册在 `"user"` 名下，查找不命中 → 用默认 (10, 1.0) 创建桶
   - 修复后 `_get_user_bucket(user_id)` 查找 `"user"` 规则获取 (capacity, refill_rate)，再按用户隔离创建桶
6. **QUEUE 策略死锁修复**（关键修复）：
   - 原实现 `_acquire_concurrent` 在 `time.sleep()` 期间持有 `_concurrent_lock`，导致 `release()` 无法获取锁 → 死锁
   - 修复后用 `threading.Condition(self._concurrent_lock)`，`wait()` 释放锁，`release()` 调用 `notify_all()` 唤醒等待者
7. **wait_time 返回级别**：无限流时返回 `(0.0, "none")` 而非 `(0.0, "global")`，避免误报限流级别
8. **模块级导入 get_business_metrics_collector**：从函数内 `from ... import` 提升到模块级 `try/except ImportError`，使 `patch('agent.rate_limiter.get_business_metrics_collector')` 可生效

**设计决策：** 用户桶用 `"user"` 规则参数 + 按用户 ID 隔离桶实例，而非共享单桶。这样既保持用户间隔离，又复用同一限流配置。

### 2.3 graceful_degrade.py — 缓存优先 + 模块默认值 + text_only 计数

**调整点：**
1. **DegradeConfig/DegradeMetrics 数据类**：替代旧 kwargs 构造，支持 `GracefulDegrade(DegradeConfig(max_retries=3, cache_ttl_seconds=300))`
2. **DegradeLevel 新增枚举值**：
   - `LENIENT (20%+)`、`CACHE_ONLY (40%+)`、`SKIP (60%+)`、`EMERGENCY (80%+)`
   - 保留旧值 `RETRY/RELAXED/FALLBACK/DISABLED` 向后兼容
3. **with_degrade 缓存优先**（关键修复）：
   - 原实现仅在 `should_degrade=True` 时检查缓存，正常路径直接调 `func` → 第二次调用不命中缓存
   - 修复后正常路径也先检查缓存，命中则直接返回，避免重复调用 `func`
4. **失败后回退缓存**（关键修复）：
   - 原实现重试全部失败后直接调 fallback，不检查缓存 → dashboard 场景返回空数据而非缓存数据
   - 修复后失败时先检查缓存，缓存有数据则返回，否则才调 fallback
5. **fallback 异常时返回模块默认值**（关键修复）：
   - 原实现 fallback 抛异常后返回 `default_fallbacks.get(component)` = None
   - 修复后返回 `_get_module_default(component)`，为每个模块预设非 None 默认值（schema → `{"valid": False, ...}`，memory → `[]` 等）
6. **SCHEMA 模块降级计入 text_only_count**：
   - 原实现仅 `level == LENIENT` 时计数
   - 修复后 `module_key == "schema"` 也计数，因为 Schema 降级语义上等于回退到纯文本

**设计决策：** 缓存优先策略选择"总是先查缓存"而非"仅降级时查缓存"，因为缓存的核心价值就是避免重复计算，不应受降级状态门控。

### 2.4 disaster_recovery.py — 枚举补全 + 多态构造

**调整点：**
1. **BackupType/RecoveryStatus 枚举**：`FULL/INCREMENTAL/SNAPSHOT` + `PENDING/IN_PROGRESS/SUCCESS/FAILED`
2. **BackupConfig/BackupInfo/RecoveryInfo 数据类**：结构化备份恢复配置
3. **多态构造**：`DisasterRecovery()` / `DisasterRecovery(BackupConfig(...))` / `DisasterRecovery(backup_dir=, config_path=)` 旧 API
4. **新增方法**：`trigger_backup/restore_from_backup/register_backup_provider/get_backup_list/repair_database/auto_recover_on_startup/get_recovery_status/get_status`
5. **ConfigHotReloader 类**：配置热重载，监听配置文件变化
6. **全局单例**：`get_disaster_recovery()/get_config_reloader()`

### 2.5 附带修复 — digital_life.py 导入缺失

`digital_life.py` 第 174 行模块级调用 `log_dict()` 但从未导入，导致 `NameError` 阻断整个 `agent` 包导入。修复：在 `from .logging_utils import ...` 中追加 `log_dict`。

## 3. 测试验证

| 模块 | 测试文件 | 通过数 |
|------|----------|--------|
| circuit_breaker | test_circuit_breaker_boundary.py | 36 |
| rate_limiter | test_rate_limiter_boundary.py | 69 |
| disaster_recovery | test_disaster_recovery_scenarios.py | 45 |
| graceful_degrade | test_graceful_degrade_scenarios.py | 30 |
| **合计** | | **180** |

## 4. 架构经验

### 4.1 兼容 shim 的三种模式

| 模式 | 适用场景 | 成本 |
|------|----------|------|
| `@property` 别名 | 字段重命名（CircuitStats） | 低，只读兼容 |
| 多态构造函数 | 构造签名变更（RateLimiter/CircuitBreaker） | 中，需判断参数类型 |
| 枚举值并存 | 枚举扩展（DegradeLevel） | 低，新旧值共存 |

### 4.2 并发原语选型

- **Lock + sleep = 死锁**：持有锁期间 sleep，其他线程无法获取锁释放资源
- **Condition + wait = 正确**：`wait()` 释放锁并阻塞，`notify_all()` 唤醒等待者
- 经验：任何需要在临界区内等待外部状态变化的场景，都必须用 Condition 而非 Lock + sleep

### 4.3 缓存策略与降级的关系

缓存不应仅作为降级时的回退，而应作为常规路径的优化。降级状态决定的是"是否跳过主调用"，而非"是否查缓存"。

### 4.4 测试 patch 的导入位置

`patch('module.func')` 要求 `func` 是 `module` 的属性。函数内 `from x import func` 不会绑定到模块属性，必须在模块级导入或定义 stub。

## 5. 后续建议

1. **API 契约扫描器**：编写脚本自动对比测试 import/构造调用与主代码定义，CI 中运行
2. **枚举值集中管理**：DegradeLevel 新旧值并存增加认知负担，建议下个迭代废弃旧值
3. **disaster_recovery 实战演练**：当前测试覆盖构造和接口形状，但未覆盖真实备份恢复流程，建议补充集成测试
