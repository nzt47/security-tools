# Redis 失败桶实现草稿（EVO-T4 降基数 · 跨重启聚合）

> 用途：将 `PromptOptimizer._failure_bucket`（进程内 dict）替换为 Redis 存储，
> 实现多副本/跨重启的连续失败计数聚合。本文为**可直接复制的实现草稿**，
> 落地时机由部署形态决定（多实例 app_server 或需要天级跨重启统计时）。
>
> **状态：已落地**。权威实现 = `agent/cognitive/failure_bucket.py`
> （commit `cce30bcf`）；本文档为设计蓝本与接入说明，与本代码保持一致。
>
> 现状（已落地）：`agent/cognitive/prompt_optimizer.py` 的 `_record_failure_bucket`
> 使用进程内 dict，单实例够用；本草稿保持其方法签名不变，仅替换存储后端。

---

## 一、存储抽象接口（FailureCounter）

```python
from typing import Protocol


class FailureCounter(Protocol):
    """失败桶存储后端协议：语义与进程内 dict 一致"""

    def incr(self, pid: str) -> int:
        """累计失败次数，返回当前值（连续失败 +1）"""
        ...

    def reset(self, pid: str) -> None:
        """成功即清零"""
        ...

    def pop(self, pid: str) -> None:
        """上报后移除键（防桶膨胀）"""
        ...
```

---

## 二、InMemoryFailureCounter（现状等价）

```python
class InMemoryFailureCounter:
    """进程内存储（当前 PromptOptimizer 内嵌逻辑的独立化）"""

    def __init__(self) -> None:
        self._d: dict[str, int] = {}

    def incr(self, pid: str) -> int:
        self._d[pid] = self._d.get(pid, 0) + 1
        return self._d[pid]

    def reset(self, pid: str) -> None:
        self._d.pop(pid, None)

    def pop(self, pid: str) -> None:
        self._d.pop(pid, None)
```

---

## 三、RedisFailureCounter（含 TTL + 异常降级）

```python
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import redis  # 可选依赖：pip install redis
except ImportError:  # pragma: no cover
    redis = None


class RedisFailureCounter:
    """Redis 存储：INCR + EXPIRE(TTL) 原子计数，异常自动降级回进程内存。

    Why 降级：Redis 不可用不阻断优化流程（与谱系/埋点一致的不阻断哲学）。
    降级语义：单次操作失败 → 该次读写走内存备用桶；连续失败计数在内存侧继续，
    恢复后新计数重新写 Redis（部分失败期间计数不跨进程，可接受）。
    """

    _KEY_PREFIX = "prompt_opt:fail:"

    def __init__(self, client=None, ttl_sec: int = 86400) -> None:
        # client 可注入（测试）；默认从 REDIS_URL 连接
        if client is not None:
            self._redis = client
        elif redis is not None:
            self._redis = redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=1, socket_timeout=1,  # 快速失败，避免阻塞主流程
            )
        else:  # redis 库未安装：直接降级内存
            self._redis = None
        self._ttl = ttl_sec
        self._memory = InMemoryFailureCounter()  # 降级备用桶

    def _key(self, pid: str) -> str:
        return f"{self._KEY_PREFIX}{pid}"

    def health_check(self) -> None:
        """强制连接校验（不经降级 catch）：连接失败直接抛异常。

        Why 不用 incr('__ping__') 做健康检查：incr 内部 catch 所有异常并降级内存，
        会把连接失败吞掉，工厂的 try/except 降级分支将永远不可达。
        """
        if self._redis is None:
            raise RuntimeError("redis client unavailable")
        self._redis.ping()  # redis.Redis.ping() 强制建立连接，失败抛 ConnectionError

    def incr(self, pid: str) -> int:
        if self._redis is None:
            return self._memory.incr(pid)
        try:
            n = self._redis.incr(self._key(pid))
            self._redis.expire(self._key(pid), self._ttl)  # TTL：防键长期滞留
            return int(n)
        except Exception:
            logger.warning("[PromptOpt] Redis incr 失败，降级内存计数 pid=%s", pid)
            return self._memory.incr(pid)

    def reset(self, pid: str) -> None:
        if self._redis is None:
            self._memory.reset(pid)
            return
        try:
            self._redis.delete(self._key(pid))
        except Exception:
            self._memory.reset(pid)

    def pop(self, pid: str) -> None:
        self.reset(pid)  # 语义一致：上报后删除键


def create_failure_store(store_type: str = None) -> object:
    """工厂：按配置创建存储，Redis 不可用时降级 InMemory（fail-open）。

    store_type: None → 读 .env PROMPT_OPT_FAILURE_STORE（memory|redis）
    """
    store_type = store_type or os.getenv("PROMPT_OPT_FAILURE_STORE", "memory")
    if store_type == "redis" and redis is not None:
        try:
            store = RedisFailureCounter()
            store.health_check()  # ping 强制连接校验，失败抛异常进入降级分支
            return store
        except Exception:
            logger.warning("[PromptOpt] Redis 健康检查失败，降级回进程内存储")
    return InMemoryFailureCounter()
```

---

## 四、PromptOptimizer 接入点（3 处改动）

```python
# 1) 构造：storage 参数替代进程内 dict（保持 failure_emit_threshold 不变）
def __init__(self, *, ..., failure_store=None):
    ...
    from redis_failure_bucket import create_failure_store  # 草稿文件路径按需
    self._failure_store = failure_store or create_failure_store()
    self._failure_bucket = None  # 移除旧 dict

# 2) _record_failure_bucket 方法体改为委托存储后端（判定逻辑不变）
def _record_failure_bucket(self, proposal):
    pid = proposal.object_id
    if proposal.status in (STATUS_NO_IMPROVEMENT, STATUS_NO_SAMPLES):
        self._failure_bucket_count = self._failure_store.incr(pid)   # 原：dict get+1
    else:
        self._failure_store.reset(pid)                               # 原：pop 清零
        return
    if self._failure_bucket_count >= self.failure_emit_threshold:
        emit_metric("yunshu_prompt_optimization_failed_prompt_total",
                    labels={"prompt_id": pid, "outcome": proposal.status,
                            "success": "true"})
        self._failure_store.pop(pid)                                 # 原：pop 防膨胀

# 3) 现有测试替换注入点：PromptOptimizer(..., failure_store=InMemoryFailureCounter())
#    等价行为；新增 RedisFailureCounter 单测（见第五节）
```

---

## 五、配置项（.env / .env.example）

```
# 失败桶存储后端：memory（默认，进程内）| redis（跨重启/多副本聚合）
PROMPT_OPT_FAILURE_STORE=memory
# Redis 连接串（仅 PROMPT_OPT_FAILURE_STORE=redis 时使用）
REDIS_URL=redis://localhost:6379/0
# 失败计数键 TTL（秒，默认 86400 = 1 天；防长期失败键滞留）
PROMPT_OPT_FAILURE_TTL=86400
```

---

## 六、测试建议

```python
# 1) RedisFailureCounter 单测（stub client，不依赖真实 Redis）
class _FakeRedis:
    def __init__(self, fail=False):
        self._d, self.fail = {}, fail
    def incr(self, k):
        if self.fail: raise ConnectionError("down")
        self._d[k] = self._d.get(k, 0) + 1
        return self._d[k]
    def expire(self, k, t): return True
    def delete(self, k): self._d.pop(k, None); return True

def test_redis_counter_incr_expire():
    c = RedisFailureCounter(client=_FakeRedis())
    assert c.incr("p1") == 1 and c.incr("p1") == 2

def test_redis_counter_degrade_on_failure():
    c = RedisFailureCounter(client=_FakeRedis(fail=True))
    assert c.incr("p1") == 1      # 降级内存，不抛异常
    assert c.incr("p1") == 2

def test_create_failure_store_redis_down_fallback():
    with patch("redis.Redis.from_url", side_effect=ConnectionError):
        assert isinstance(create_failure_store("redis"), InMemoryFailureCounter)

def test_prompt_optimizer_with_redis_store(tmp_path, monkeypatch):
    # PromptOptimizer(failure_store=RedisFailureCounter(client=_FakeRedis()))
    # 复用 TestFailureBucket 5 例断言（行为等价）
```

---

## 七、权衡与注意事项

- **部分失败语义**：`incr` 成功但 `expire` 失败 → 键无 TTL，会长期滞留。
  缓解：`expire` 失败时该次也走内存计数并警告（代价：计数可能重复）。简版草稿
  保持"expire 失败不降级"，运维以监控 `redis` 指标兜底。
- **TTL 与"连续失败"语义**：TTL=1 天意味着"连续"窗口为 1 天，超过 1 天未再失败
  自动清零，符合"短窗口失败模式识别"定位；如需更长窗口调大 TTL。
- **多副本一致性**：`INCR` 原子保证多实例计数唯一；内存降级期间计数不跨进程，
  恢复后从新基线继续——可接受（降级是例外路径）。
- **健康检查开销**：`create_failure_store` 构造时一次 `incr/pop __ping__`，之后
  每次操作自然降级，无需心跳。

---

## 八、落地验证记录（2026-08-12）

> 权威实现对应 commit `cce30bcf`（failure_bucket.py / prompt_optimizer.py 接入），
> 以下为落地后的实证记录，均可复现。

### 8.1 Redis 连接超时降级（非完全不可用）✅

`_TimeoutRedis` stub（`ping/incr/expire/delete` 全部抛超时）两个场景 PASS：

- **场景 A（构造期超时）**：`create_failure_store` → `health_check().ping()` 抛
  `TimeoutError` → 工厂 `except Exception` 捕获 → 降级 `InMemoryFailureCounter`。
  证明连接超时（而非仅"完全不可用"）同样走通降级分支。
- **场景 B（运行期超时）**：注入失败后端后连续 3 次 `incr` 均超时 → 每次降级
  内存计数（日志 `Redis incr 失败，降级内存计数 pid=p1` ×3）→ 阈值 3 达标后
  经 `emit_metric` 产出 1 条 `prompt_id=p1`，全程无异常。

结论：**超时与完全不可用降级路径一致（fail-open），不阻断优化流程。**

### 8.2 TTL 过期后计数重置（连续 86401 秒）✅

带 TTL 过期语义 + 可推进时钟的假 Redis（`ttl_sec=86400` 与生产一致）验证：

- **窗口内连续失败**：`incr`→1，推进 500s 后 `incr`→2（距上次 < 86400s 仍连续）。
- **连续 86401 秒后**：总跨度 86901s 超过 86400s 窗口 → 键被服务端过期回收 →
  下一次 `incr` 返回 **1**（计数重置，重新开始新窗口）。
- **滑动窗口对照**：每次失败都刷新 TTL——推进 86000s 后 `incr`（刷新窗口），
  再推进 1000s 后 `incr` 仍连续 +1；证明"连续"以**最后一次失败**为起点，
  非固定周期。

结论：**TTL=1 天为滑动窗口；窗口过期后计数自动归零，无需人工清理。**

### 8.3 内存桶并发安全（加锁）✅

`InMemoryFailureCounter` 的 `incr` 为「读-改-写」序列，CPython 下非原子，
多线程并发评估同一 prompt 时可能**丢失更新**（如两线程同时读到 0，各写 1，
最终 1 而非 2）。已加 `threading.Lock` 保护（锁内仅内存 dict 操作，无 I/O/回调，
符合持锁纪律）；并发压力测试（8 线程 × 1000 次 incr）断言总数 = 8000 无丢失。
Redis 路径 `INCR` 本身原子，无需锁；降级备用桶共享同一把锁。

### 8.4 tool_calling 连续失败计数审计（纠正：局部变量，无需加锁）✅

上轮并发风险审计曾将 `_consecutive_failures` 误标为"模块级"（仅看了使用处
L462-467，未看定义处）。经完整检查纠正：

- `_consecutive_failures` 是 `chat_with_steps` 方法内**局部变量**（L264，每次调用
  独立创建），多线程并发调用时各对话互不共享；读-改-写在单线程循环内顺序执行，
  **无跨线程竞态**。
- 模拟验证（64 线程逻辑片段 + 16 线程真实方法并发）：各对话计数独立准确；对照
  演示若误改为共享 dict 则计数合并污染（共享场景计数合并至 156 vs 期望每线程 3）。
- 单测 `TestConsecutiveFailureIsolation`（tests/unit/test_tool_calling_comprehensive.py）
  断言：共享 service 实例 8 线程并发各对话计数独立触发，L467/L518 warning 配对，
  触发周期计数恒为 2 不跨线程累积。

结论：**不加锁**——锁保护局部变量无意义（违简易）；若未来将计数提升为实例/模块
级共享状态，须同步引入锁或原子计数。对应 commit `a02cf85f`。

### 8.5 subagent/lifecycle.py 无锁计数与 TOCTOU（加锁修复）✅

审计清单 B 类最后一项。`SubagentLifecycleManager` 两处真实竞态：

- **`_total_created` / `_total_destroyed`**：`+= 1` 为「读-改-写」序列（非原子），
  并发 create/destroy 丢更新，`get_stats()` 计数失真。
- **容量检查 TOCTOU**：`create` 的「检查 `len(_subagents) < max_subagents` → 写入
  dict」两步骤间可被另一线程插入，并发下活跃数**超卖超过 max_subagents**
  （资源耗尽防护失效）。查询方法迭代 `_subagents` 与 destroy 并发还会抛
  RuntimeError（dict changed size during iteration）。

修复（commit `f426cfde`）：统一 `threading.RLock` 保护 `_subagents` 与计数，
覆盖 create / destroy / hot_reload（改名 pop/insert）/ gc（RLock 重入 destroy）/
全部查询方法（get / get_by_id / list / count / get_stats 等）——读取锁内快照。
RLock 选型原因：`gc → destroy` 同线程重入不互锁。锁内仅内存 dict/整数变更与
纯内存容器构建（`SubagentContainer.__init__`、`get_memory_delta()` 均无 I/O），
logger 移出锁块，符合持锁纪律。

验证（tests/unit/test_subagent.py `TestSubagentLifecycleConcurrency`，4 用例）：
- 30 线程 × 20 次并发 create：`count() == 600`、`total_created == 600` 无丢失；
- 100 线程并发 create（max=20）：**恰好放行 20 个**（拒绝 80），`count() == 20`
  不超卖——无锁时此用例必然超卖；
- create+destroy 混合：`total_created - total_destroyed == count()` 守恒；
- 4 读 + 4 写混合：查询不抛 RuntimeError，计数守恒。

### 8.6 全量并发审计结论（2026-08-13）📋

B 类处理完毕后对 `agent/` 全目录（388 个 Python 文件）做了第二轮并发审计（四路 grep
候选 + 逐一读源码核验，交叉验证无锁共享状态 32 处）。按严重度分组：

**高（5 处，影响业务正确性，需修复）**
- `model_router/cost_tracker.py`：`record()` 的 `_daily_stats` 读-改-写 + TOCTOU
  （L40-44），模块级单例——费用/调用计数并发丢失（关键业务数据）；
- `orchestrator/orchestrator.py`：`_interaction_count += 1`（L251）全文件 14 处引用
  均无锁，且用作 trace `interaction_id`（L369/978/1979）——并发下轮次计数失真、
  interaction_id 重复（与 AsyncSaveMonitor task_id 同类问题）；
- `monitoring/performance.py` `LLMCache`（L664-707）：无锁 OrderedDict
  move_to_end/popitem/put 并发 → 结构损坏 RuntimeError（注意：与已加锁的
  `llm_response_cache.LLMResponseCache` 是**不同类**）；
- `utils/index_manager.py`：`add_item`/`remove_item` 无锁 defaultdict 变更 +
  `remove_item` 检查后 del 的 TOCTOU（KeyError）；
- `workflow_learning/matcher.py`：`_rebuild` 迭代 `_docs` 时另一线程 `add()` →
  RuntimeError，`_dirty` 整表重建无锁。

**中（11 处，计数/统计失真或结构风险，建议修复）**
`web/crawler_control.py`（统计+UA/代理列表）、`web/search.py`（缓存 TOCTOU+整体
重绑定）、`health/assessor.py`（`_history.append/pop(0)` 结构损坏）、`network_config.py`
（缓存 TOCTOU）、`multi_tenant.py`（dict 变更+全量 dump 无原子写）、`safety_guard.py`、
`permission_system.py`（计数+告警历史重绑定）、`utils/sensitive_data_filter.py`
（迭代中改 dict → RuntimeError）、`monitoring/search.py`（`_performance_history` 追加）、
`cognitive/reflection.py`（`_retry_counts` 读-改-写 + 删除 TOCTOU）、`memory/router.py`
（adapters dict 注册/遍历）。

**低（16 处，软指标可接受 / 单线程 / 视调用方式）**
`p6` 快照频率、`workflow_learning/models.py` 成功率、`log_system/analyzer.py` 命中
计数、`mcp_executor.py`/`extensions/manager.py` get-or-create、`tools/mcp_connector.py`
连接 dict、`behavior_controller.py` 模式切换、`orchestrator/lifecycle_manager.py`
engine_health、`cognitive/knowledge.py`（asyncio 单线程语义安全）、
`log_system/introspection.py` `_run_count`（单线程）、`health/health_score.py`、
`task_planner/dag.py`、`monitoring/performance.py` AsyncSaveMonitor/PerformanceLogger
（list append/pop，结构风险但计数器按软指标）。

修复策略建议（不变接口签名）：高优先 `cost_tracker` → `orchestrator` → `LLMCache`
→ `index_manager` → `matcher`；与既有系列同模式（RLock 锁内仅内存操作）。

### 8.7 高危 5 处全部修复完成（2026-08-13）✅

第二轮审计「高（5 处）」已全部落地，每处均含「加锁修复 + 高并发验证测试 + 独立提交」：

| # | 文件 | 竞态 | 修复方式 | 验证（并发测试） | 提交 |
|---|---|---|---|---|---|
| 1 | `model_router/cost_tracker.py` | `_daily_stats` 读-改-写 + TOCTOU | `threading.Lock`，`setdefault` 原子初始化，文件写锁外 | 100 线程×50 次 record 计数精确、100 线程首日并发只初始化一次、多模型按单价精确、读写混合守恒 | `c3d04164` |
| 2 | `orchestrator/orchestrator.py` | `_interaction_count += 1` 丢更新 + interaction_id 重复 | 递增在宿主创建的 `_interaction_lock` 锁内（LifecycleManager/V2 双初始化点） | 100 线程×50 次计数精确、递增取回值 5000 个全局唯一、宿主锁创建静态断言 | `ac7ae7c8` |
| 3 | `monitoring/performance.py` `LLMCache` | OrderedDict 结构损坏 RuntimeError + 容量 TOCTOU | `threading.Lock`，get/put/clear/get_top_patterns 锁化，clear 日志锁外 | 100 线程 put 容量≤50 不超限+逐出守恒、同 key 并发无膨胀、100 线程 get 命中精确、读写混合守恒 | `a6ee5860` |
| 4 | `utils/index_manager.py` | defaultdict 变更 + `remove_item` 检查后 del 的 TOCTOU | `threading.RLock`，全部索引操作锁化，`_tokenize` 锁外 | 100 线程×10 次计数精确+检索完整、读写混合不崩溃、100 线程重复 remove 无 KeyError、index+remove 混合一致 | `656143f0` |
| 5 | `workflow_learning/matcher.py` | `_rebuild` 迭代 `_docs` 与 `add()` 并发 → RuntimeError | `WorkflowMatcher` RLock：register/unregister/rebuild 整体锁化，match 仅 query 段持锁（观测/日志/metrics 锁外） | 100 线程×10 次注册计数精确+索引完整、4 写+4 读不崩溃、20 线程重复 unregister+match 安全、register+rebuild 并发无孤儿 | `ee571c48` |

共同模式（与 B 类 4 处一致）：锁内仅内存 dict/整数变更（持锁纪律：锁内无 I/O、无外部
回调），日志/metrics 移出锁块；竞态可验证性靠测试的确定性断言（计数精确/唯一性/容量
不超限），压测提供性能佐证。9 处竞态全部闭环，共 10 个提交。

### 8.8 第三轮全量并发审计结论（2026-08-13）📋

高危 5 处闭环后对 `agent/` 全目录（388 个 Python 文件）做第三轮独立审计（search 子代理
全目录扫描 + 人工核验重点文件，与第二轮 32 处清单交叉比对）。结论：**前两轮成果稳固**
（30+ 已加锁文件/模块复核无回退，9 处已处理竞态全部在位）；**仍有 12 处无锁共享状态**，
其中 6 处因核实调用方后由第二轮"中危"升级为"高危"（崩溃级/真实并发热路径）：

**高危 6 处（未处理，建议优先）**
- `safety_guard.py`：`_alert_callbacks` 模块级 list，`register_alert_callback`（任意线程）
  append 与 `_record_alert`（告警线程）迭代并发 → **RuntimeError**；且迭代中执行外部回调
  （即便加锁也须锁外派发，遵守持锁纪律）；
- `health/assessor.py`：`_history.append + pop(0)` 读-改-写并发 → **IndexError/历史错乱**
  （自愈判定依据被污染）；模块级单例被 5+ 路由/采集/自愈多路调用；
- `cognitive/reflection.py`：`_retry_counts` get→+1→写回 TOCTOU → 重试计数丢失（可能无限
  重试），del/pop 与读取并发 KeyError；
- `web/search.py`：搜索热路径 `_stats["searches"] += 1`、缓存整体重建 `_cache = {**...}`
  （覆盖丢失窗口）、引擎注册/移除与搜索并发；
- `web/crawler_control.py`：`_stats`/`_domain_delays`/`_proxy_stats`/`_robots_cache` 无锁
  （仅限速 `_rate_lock` 已加锁），`can_fetch` 若加锁须先取结果再锁内写缓存（防锁内 I/O）；
- `multi_tenant.py`：`delete_tenant` 递归删除 + `del dict[key]` 与遍历并发 → **RuntimeError**，
  三个管理器均为模块级单例由 HTTP 路由触发（真实并发）。

**中危 6 处（未处理，计数/结构风险）**
`permission_system.py`（`_permission_log` append + 计数）、`network_config.py`（缓存 TOCTOU，
并发度低）、`monitoring/search.py`（后台监控线程 append/`+=` vs HTTP 读，软指标）、
`orchestrator/dialog_state.py`（`_SESSION_STATES` 懒创建 TOCTOU + `turn_count += 1`，真实
HTTP 会话并发）、`monitoring/optimized_metrics.py` `_stats` 字典（LockFreeCounter 已修，
仅此组计数遗漏，软指标）、`memory/router.py`（适配器注册 TOCTOU + 懒初始化，启动期为主）。

**低危 6 处（安全/良性，无需处理）**：`utils/sensitive_data_filter.py`（无状态单例）、
`digital_life.py` `_module_import_results`（导入期写入/运行期只读）、`monitoring/__init__.py`
`_LAZY_IMPORTS`（import lock 保证）、`skills_mgmt/evaluator.py` `_executor_cache`（单键 put
原子，重复构造无害）、`monitoring/self_healer.py` `_action_locks`（单键 put 原子）、
`cognitive/failure_collector.py`（已加锁但**持锁调用外部回调**——违反持锁纪律，建议改
"锁内收集、锁外派发"，属纪律项非竞态）。

建议优先级：崩溃级 `safety_guard` / `multi_tenant` / `health/assessor` → 业务正确性
`reflection` / `web/search` / `web/crawler_control` → 软指标 6 处。与既有系列同修复模式。

