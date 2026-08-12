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
