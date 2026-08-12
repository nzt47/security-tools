"""失败桶存储后端（EVO-T4 降基数 · 跨重启聚合）

【不易】FailureCounter 语义固定：失败 +1 / 成功清零 / 上报后移除。
【变易】存储后端可切换：memory（默认，单实例）↔ redis（多副本/跨重启聚合）；
        Redis 不可用时降级回内存（fail-open），不阻断优化流程。
【简易】只暴露 incr/reset/pop 三个操作，判定阈值逻辑留在 PromptOptimizer 侧。

落地依据：docs/observability/redis_failure_bucket_draft.md
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import redis  # 可选依赖：pip install redis
except ImportError:  # pragma: no cover 未安装时走内存后端
    redis = None


class FailureCounter:
    """失败桶存储后端协议（duck-typing 即可，本类仅作类型标注）"""

    def incr(self, pid: str) -> int:
        """累计失败次数，返回当前值（连续失败 +1）"""
        raise NotImplementedError

    def reset(self, pid: str) -> None:
        """成功即清零"""
        raise NotImplementedError

    def pop(self, pid: str) -> None:
        """上报后移除键（防桶膨胀）"""
        raise NotImplementedError


class InMemoryFailureCounter:
    """进程内存储（单实例默认后端，语义与历史 PromptOptimizer._failure_bucket 一致）"""

    def __init__(self) -> None:
        self._d: dict = {}

    def incr(self, pid: str) -> int:
        self._d[pid] = self._d.get(pid, 0) + 1
        return self._d[pid]

    def reset(self, pid: str) -> None:
        self._d.pop(pid, None)

    def pop(self, pid: str) -> None:
        self._d.pop(pid, None)


class RedisFailureCounter:
    """Redis 存储：INCR + EXPIRE(TTL) 原子计数，异常自动降级内存（fail-open）

    Why 降级：Redis 不可用不阻断优化流程（与谱系/埋点一致的不阻断哲学）。
    降级语义：单次操作失败 → 该次读写走内存备用桶；连续失败计数在内存侧继续，
    恢复后新计数重新写 Redis（部分失败期间计数不跨进程，可接受）。
    """

    _KEY_PREFIX = "prompt_opt:fail:"

    def __init__(self, client=None, ttl_sec: Optional[int] = None) -> None:
        # client 可注入（测试）；默认从 REDIS_URL 连接，1s 快速失败避免阻塞主流程
        if client is not None:
            self._redis = client
        elif redis is not None:
            self._redis = redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=1, socket_timeout=1,
            )
        else:  # redis 库未安装：直接降级内存
            self._redis = None
        self._ttl = ttl_sec if ttl_sec is not None \
            else _env_int("PROMPT_OPT_FAILURE_TTL", 86400)
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


def create_failure_store(store_type: Optional[str] = None) -> object:
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


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default
