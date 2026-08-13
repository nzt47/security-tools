"""失败桶存储后端单元测试（EVO-T4 服务端失败桶降基数）

覆盖：
    1. RedisFailureCounter：INCR + EXPIRE TTL、异常降级内存（fail-open）
    2. health_check：ping 强制校验（不经降级 catch）
    3. create_failure_store 工厂：默认 memory / Redis 不可用降级 InMemory
    4. PromptOptimizer 注入 Redis 后端集成（连续失败触发行为等价）
"""
import threading
from unittest.mock import patch

import pytest

from agent.cognitive import failure_bucket as fb
from agent.cognitive.failure_bucket import (
    InMemoryFailureCounter,
    RedisFailureCounter,
    create_failure_store,
)
from agent.cognitive.prompt_optimizer import (
    PromptOptimizationProposal,
    PromptOptimizer,
    STATUS_NO_IMPROVEMENT,
)


# ════════════════════════════════════════════════════════════
#  辅助
# ════════════════════════════════════════════════════════════

class _FakeRedis:
    """可注入 stub：fail=True 时所有命令抛 ConnectionError（模拟 Redis 不可用）"""

    def __init__(self, fail=False):
        self._d = {}
        self.fail = fail
        self.expired = []

    def ping(self):
        if self.fail:
            raise ConnectionError("redis down")

    def incr(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        self._d[key] = self._d.get(key, 0) + 1
        return self._d[key]

    def expire(self, key, ttl):
        if self.fail:
            raise ConnectionError("redis down")
        self.expired.append((key, ttl))
        return True

    def delete(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        self._d.pop(key, None)
        return True


def _fail_prop(pid="p1"):
    return PromptOptimizationProposal(
        proposal_id="ppo-t", object_id=pid, original_prompt="orig",
        suggested_prompt=None, original_score=0.5, suggested_score=None,
        improvement=0.0, status=STATUS_NO_IMPROVEMENT,
        comparison="paired", source="evaluator", reason="t",
        category="search", sample_count=1)


# ════════════════════════════════════════════════════════════
#  RedisFailureCounter
# ════════════════════════════════════════════════════════════

class TestRedisFailureCounter:
    def test_incr_expire_ttl(self):
        """可用时计数走 Redis，每次 incr 后设置 TTL"""
        c = RedisFailureCounter(client=_FakeRedis(), ttl_sec=3600)
        assert c.incr("p1") == 1
        assert c.incr("p1") == 2
        assert c._redis.expired == [("prompt_opt:fail:p1", 3600),
                                    ("prompt_opt:fail:p1", 3600)]

    def test_incr_degrade_on_failure(self):
        """Redis 不可用 → 单次 incr 降级内存计数，不抛异常"""
        c = RedisFailureCounter(client=_FakeRedis(fail=True))
        assert c.incr("p1") == 1
        assert c.incr("p1") == 2

    def test_reset_pop_no_raise_on_failure(self):
        """Redis 不可用时 reset/pop 静默降级，不抛异常"""
        c = RedisFailureCounter(client=_FakeRedis(fail=True))
        c.incr("p1")
        c.reset("p1")  # 不抛
        c.incr("p1")
        c.pop("p1")    # 不抛

    def test_redis_none_fallback_memory(self):
        """client 为 None 且 redis 库不可用 → 直接用内存计数"""
        c = RedisFailureCounter(client=None)
        c._redis = None
        assert c.incr("p1") == 1


class TestHealthCheck:
    def test_ping_failure_raises(self):
        """ping 失败直接抛异常（不经降级 catch，供工厂降级分支捕获）"""
        c = RedisFailureCounter(client=_FakeRedis(fail=True))
        with pytest.raises(ConnectionError):
            c.health_check()

    def test_ping_ok_no_raise(self):
        c = RedisFailureCounter(client=_FakeRedis())
        c.health_check()  # 不抛


class TestCreateFailureStore:
    def test_default_memory(self, monkeypatch):
        """默认（memory）→ InMemoryFailureCounter"""
        monkeypatch.delenv("PROMPT_OPT_FAILURE_STORE", raising=False)
        assert isinstance(create_failure_store(), InMemoryFailureCounter)

    def test_redis_healthy_returns_redis(self, monkeypatch):
        """Redis 可用 → RedisFailureCounter（注入 stub 构造）"""
        monkeypatch.setattr(fb, "redis", object())
        monkeypatch.setattr(
            fb, "RedisFailureCounter",
            lambda: RedisFailureCounter(client=_FakeRedis()))
        assert isinstance(create_failure_store("redis"), RedisFailureCounter)

    def test_redis_down_fallback_memory(self, monkeypatch):
        """Redis 构造失败（模拟连接失败）→ 降级 InMemory"""
        monkeypatch.setattr(fb, "redis", object())

        def boom(*a, **k):
            raise ConnectionError("down")
        monkeypatch.setattr(fb, "RedisFailureCounter", boom)
        assert isinstance(create_failure_store("redis"), InMemoryFailureCounter)

    def test_redis_health_check_down_fallback_memory(self, monkeypatch):
        """Redis 可用但 health_check ping 失败 → 降级 InMemory"""
        monkeypatch.setattr(fb, "redis", object())
        monkeypatch.setattr(
            fb, "RedisFailureCounter",
            lambda: RedisFailureCounter(client=_FakeRedis(fail=True)))
        assert isinstance(create_failure_store("redis"), InMemoryFailureCounter)


# ════════════════════════════════════════════════════════════
#  PromptOptimizer 集成
# ════════════════════════════════════════════════════════════

class TestPromptOptimizerWithRedisStore:
    def _make_opt(self, fail=False, threshold=3):
        store = RedisFailureCounter(client=_FakeRedis(fail=fail))
        return PromptOptimizer(
            evaluator=object(),  # 不执行评估，仅测桶逻辑
            failure_emit_threshold=threshold, failure_store=store), store

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_redis_store_continuous_failures_emit_once(self, mock_emit, tmp_path):
        """注入 Redis 后端：连续失败 3 次 → 触发一次且上报后移除"""
        opt, store = self._make_opt()
        for _ in range(3):
            opt._record_failure_bucket(_fail_prop("p1"))
        prompts = [c.kwargs.get("labels", {}).get("prompt_id")
                   for c in mock_emit.call_args_list
                   if "failed_prompt_total" in str(c)]
        assert prompts == ["p1"]
        assert store._redis._d == {}  # 键已删除

    @patch("agent.skills_mgmt.observability.emit_metric")
    def test_redis_down_still_emits_after_three(self, mock_emit, tmp_path):
        """Redis 运行期不可用 → 降级内存，3 次失败仍正确触发，无异常"""
        opt, _ = self._make_opt(fail=True)
        for _ in range(3):
            opt._record_failure_bucket(_fail_prop("p1"))
        prompts = [c.kwargs.get("labels", {}).get("prompt_id")
                   for c in mock_emit.call_args_list
                   if "failed_prompt_total" in str(c)]
        assert prompts == ["p1"]


# ════════════════════════════════════════════════════════════
#  InMemoryFailureCounter 并发安全（threading.Lock）
# ════════════════════════════════════════════════════════════

class TestInMemoryConcurrency:
    def test_concurrent_incr_no_lost_update(self):
        """8 线程 × 1000 次并发 incr：锁保证读-改-写原子，无丢失更新"""
        c = InMemoryFailureCounter()
        n_threads, per = 8, 1000
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()  # 同步起跑，放大竞态窗口
                for _ in range(per):
                    c.incr("p1")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert c._d["p1"] == n_threads * per  # 无丢失更新

    def test_concurrent_reset_pop_mixed_no_raise(self):
        """并发 incr/reset/pop 混合操作不抛异常（锁内仅 dict 操作）"""
        c = InMemoryFailureCounter()
        errors = []

        def worker():
            try:
                for _ in range(500):
                    c.incr("p1")
                    c.reset("p1")
                    c.incr("p2")
                    c.pop("p2")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # 终态合法：p1 计数非负（最后一次操作可能是 reset 置空，也可能残留）
        assert c._d.get("p1", 0) >= 0
