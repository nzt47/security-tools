"""统一跨进程锁原语测试（TASK-S8-02 步骤 2/5）

覆盖：
- 非阻塞 ``try_lock()``：拿不到**立刻**返回 False（不是排队）；
- 有限等待 ``locked(timeout)`` / ``acquire(timeout)``：超时**显式失败**并留痕；
- 跨**线程**互斥（Windows ``msvcrt`` 字节锁按进程判定，必须靠进程内 RLock 兜）；
- 跨**进程**互斥（真正起多进程，不是线程模拟）；
- 同线程重入的**配对释放**（防锁泄漏——实现期实测缺陷）；
- **持锁进程被杀 → 锁自动可恢复、无死锁**（硬约束 2）；
- 陈旧锁判定无副作用（不创建锁文件）；
- 降级路径留痕（硬约束：锁超时/冲突不得静默）。

【用例落盘纪律（S3-02/S3-03 两次污染教训）】所有用例**显式传 tmp_path**，
绝不触碰 ``data/`` 真实运行目录；并用 autouse fixture 重置可观测计数与留痕钩子。
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time

import pytest

from agent.utils.cross_process_lock import (
    CrossProcessLock,
    LockFileError,
    LockTimeout,
    LockUnavailable,
    lock_metrics,
    lock_path_for,
    reset_conflict_cooldown,
    reset_lock_metrics,
    set_notify_hook,
)

pytestmark = [pytest.mark.unit, pytest.mark.p2]

#: Windows 的进程创建比 POSIX 慢得多（spawn + 解释器启动），用例上界需留足
_MP_TIMEOUT = 90.0


# ════════════════════════════════════════════════════════════
#  隔离
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate():
    """每例重置锁计数/冷却 + 静音留痕（留痕本身另有专门用例断言）"""
    reset_lock_metrics()
    reset_conflict_cooldown()
    set_notify_hook(lambda action, detail: None)
    yield
    set_notify_hook(None)
    reset_lock_metrics()
    reset_conflict_cooldown()


@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / "guarded.lock")


class _BackgroundHolder:
    """在**独立线程**里持锁，并由该线程自行释放

    【为什么需要它（实现期修正）】锁是**线程亲和**的：``release()`` 只允许
    持有线程调用（否则会绕过 RLock 属主检查破坏外层状态）。因此"主线程持锁、
    另一个线程释放"这种测试写法是**非法用法**，会把用例变成假失败。
    本辅助类让持有与释放发生在同一线程，从而能干净地构造"竞争者视角"。
    """

    def __init__(self, path: str, name: str = "holder"):
        self.path = path
        self.name = name
        self.acquired = threading.Event()
        self._release_now = threading.Event()
        self._done = threading.Event()
        self._ok = False
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    def __enter__(self) -> "_BackgroundHolder":
        self.thread.start()
        assert self.acquired.wait(timeout=10.0), "后台线程未能拿到锁"
        assert self._ok, "后台线程 try_lock 失败（前置条件不成立）"
        return self

    def __exit__(self, *exc) -> None:
        self._release_now.set()
        self._done.wait(timeout=10.0)

    def release_after(self, delay_s: float) -> None:
        """让后台线程在 delay_s 秒后自行释放"""
        threading.Timer(delay_s, self._release_now.set).start()

    def _run(self) -> None:
        lock = CrossProcessLock(self.path, name=self.name)
        self._ok = lock.try_lock()
        self.acquired.set()
        try:
            self._release_now.wait(timeout=60.0)
        finally:
            lock.release()
            self._done.set()


# ════════════════════════════════════════════════════════════
#  1. 语义：非阻塞 / 有限等待
# ════════════════════════════════════════════════════════════


def test_try_lock_succeeds_when_free(lock_path):
    lock = CrossProcessLock(lock_path, name="t")
    assert lock.try_lock() is True
    assert lock.held is True
    assert lock.release() is True
    assert lock.held is False


def test_try_lock_is_non_blocking_not_queuing(lock_path):
    """核心语义：第二个持有者**被拒**，而不是排队等待（守卫/降级的前提）

    判据是**耗时**而非返回值：非阻塞路径必须在毫秒级返回（排队实现会等
    到持有者释放为止——本用例的持有者持锁 60s，若为阻塞式必然超时失败）。
    """
    with _BackgroundHolder(lock_path) as holder:
        contender = CrossProcessLock(lock_path, name="contender")
        t0 = time.monotonic()
        ok = contender.try_lock()
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        assert holder._ok
    assert ok is False, "竞争者在持有者持锁期间不应拿到锁"
    assert elapsed_ms < 500.0, f"非阻塞路径耗时 {elapsed_ms:.1f}ms（疑似排队）"


def test_acquire_zero_raises_unavailable(lock_path):
    with _BackgroundHolder(lock_path):
        with pytest.raises(LockUnavailable):
            CrossProcessLock(lock_path, name="c").acquire(0)


def test_locked_timeout_raises_locktimeout(lock_path):
    """有限等待超时 → **显式失败**（LockTimeout），不是静默返回"""
    with _BackgroundHolder(lock_path):
        t0 = time.monotonic()
        with pytest.raises(LockTimeout):
            with CrossProcessLock(lock_path, name="c").locked(timeout=0.3):
                pytest.fail("不应进入临界区")
        elapsed = time.monotonic() - t0
    assert 0.2 <= elapsed < 10.0, f"超时边界异常: {elapsed:.2f}s"


def test_locked_degrade_does_not_raise(lock_path):
    """``on_timeout='degrade'``：调用方自己走降级（仍留痕，只是不抛）"""
    with _BackgroundHolder(lock_path):
        with CrossProcessLock(lock_path, name="c").locked(
                0.1, on_timeout="degrade") as ctx:
            assert ctx.acquired is False


def test_locked_succeeds_after_release(lock_path):
    """有限等待在持有者释放后**应当**拿到锁（不是一拒了之）"""
    with _BackgroundHolder(lock_path) as holder:
        holder.release_after(0.2)
        waiter = CrossProcessLock(lock_path, name="waiter")
        t0 = time.monotonic()
        with waiter.locked(timeout=20.0):
            # 断言必须在临界区内：退出 with 会释放锁，held 随即变 False
            assert waiter.held is True
            waited = time.monotonic() - t0
        assert waiter.held is False
    assert 0.05 <= waited < 15.0, f"等待时长异常: {waited:.2f}s"


def test_invalid_on_timeout_rejected(lock_path):
    with pytest.raises(ValueError):
        CrossProcessLock(lock_path).locked(0.1, on_timeout="ignore")


# ════════════════════════════════════════════════════════════
#  2. 跨线程互斥（进程内 RLock）
# ════════════════════════════════════════════════════════════


def test_cross_thread_exclusion_within_process(lock_path):
    """同进程两个线程不得同时进入临界区

    【为什么必须有用例】Windows 的 ``msvcrt.locking`` 按**进程**判定字节区间：
    同进程两个线程用两个 fd 锁同一区域，第二个**不会**被 OS 拒（实测）。
    唯一的串行化来自本模块的进程内 RLock —— 这条用例就是它的守卫。
    """
    inside = []
    concurrent = []

    def worker(tag):
        lock = CrossProcessLock(lock_path, name=tag)
        for _ in range(20):
            with lock.locked(timeout=10.0):
                if inside:
                    concurrent.append((tag, list(inside)))
                inside.append(tag)
                time.sleep(0.001)
                inside.remove(tag)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
    assert not concurrent, f"检测到同进程并发进入临界区: {concurrent[:3]}"
    assert all(not t.is_alive() for t in threads)


def test_reentrant_acquire_by_same_thread_balanced(lock_path):
    """同线程重入：允许，且**必须配对释放**（防锁泄漏）

    【实现期实测缺陷】第一版用"路径级深度 > 1"判定重入并立即把实例标记为
    未持有 ⇒ 外层 ``release()`` 直接短路返回，**OS 锁永不释放**（锁泄漏、
    进程内互斥静默失效）。本用例锁死"重入 N 层 = 释放 N 层才真解锁"。
    """
    lock = CrossProcessLock(lock_path, name="r")
    assert lock.try_lock() is True
    assert lock.try_lock() is True          # 重入

    lock.release()
    assert lock.held is True, "内层释放后外层仍应持有"

    # 另一线程此时仍不得进入
    got = {}
    threading.Thread(target=lambda: got.update(
        ok=CrossProcessLock(lock_path, name="x").try_lock())).start()
    time.sleep(0.05)
    assert got.get("ok") is False, "外层仍持有时不应放行"

    lock.release()
    assert lock.held is False
    # 真正的解锁：另一个线程现在必须能拿到（否则就是泄漏）
    other = CrossProcessLock(lock_path, name="y")
    assert other.try_lock() is True, "配对释放后仍拿不到锁 = 锁泄漏"
    other.release()


def test_non_owner_thread_release_refused(lock_path):
    """非属主线程释放被拒（否则会绕过 RLock 属主检查破坏外层状态）"""
    lock = CrossProcessLock(lock_path, name="owner")
    assert lock.try_lock()
    done = {}

    def stranger():
        done["result"] = lock.release()

    t = threading.Thread(target=stranger)
    t.start()
    t.join(timeout=5.0)
    assert done["result"] is False
    assert lock.held is True, "非属主释放不得改变持有状态"
    lock.release()


# ════════════════════════════════════════════════════════════
#  3. 跨进程互斥（真进程，非线程模拟）
# ════════════════════════════════════════════════════════════


def _hold_lock(path: str, hold_s: float, ready) -> None:  # pragma: no cover - 子进程
    """子进程：拿到锁 → 通知父进程 → 持有 hold_s 秒 → 释放"""
    from agent.utils.cross_process_lock import CrossProcessLock
    lock = CrossProcessLock(path, name="child")
    ok = lock.try_lock()
    ready.put(ok)
    if ok:
        time.sleep(hold_s)
        lock.release()


def test_cross_process_exclusion(lock_path):
    """另一个**进程**持有锁时，本进程必须被拒（真正跨进程，非线程模拟）"""
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    proc = ctx.Process(target=_hold_lock, args=(lock_path, 2.0, ready))
    proc.start()
    try:
        assert ready.get(timeout=_MP_TIMEOUT) is True, "子进程未能拿到锁"
        mine = CrossProcessLock(lock_path, name="parent")
        assert mine.try_lock() is False, "跨进程互斥失效：父进程也拿到了锁"
        # 子进程释放后，父进程应能拿到（有限等待语义）
        with mine.locked(timeout=20.0):
            assert mine.held is True
    finally:
        proc.join(timeout=_MP_TIMEOUT)
        if proc.is_alive():  # pragma: no cover - 兜底，避免留下孤儿进程
            proc.terminate()
            proc.join(timeout=10.0)


def test_lock_recoverable_after_holder_killed(lock_path):
    """**持锁进程被杀 → 锁可恢复、无死锁**（硬约束 2 的核心用例）

    机制：OS 文件锁随进程消亡由内核自动释放，因此"被杀"不会留下需要人工清理
    的残留状态。本用例真的 ``kill`` 子进程（不是正常退出），再断言父进程能在
    有限时间内拿到锁。
    """
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    proc = ctx.Process(target=_hold_lock, args=(lock_path, 60.0, ready))
    proc.start()
    try:
        assert ready.get(timeout=_MP_TIMEOUT) is True, "子进程未能拿到锁"
        mine = CrossProcessLock(lock_path, name="parent")
        assert mine.try_lock() is False, "前置条件：子进程正持锁"

        proc.terminate()          # = 持锁进程被杀
        proc.join(timeout=_MP_TIMEOUT)
        assert not proc.is_alive()

        t0 = time.monotonic()
        assert mine.acquire(10.0) is not None, "持锁进程被杀后锁未恢复（疑似死锁）"
        recovered_ms = (time.monotonic() - t0) * 1000.0
        mine.release()
        # 上界放宽：Windows 上 terminate 到内核回收句柄有调度延迟
        assert recovered_ms < 10000.0, f"恢复耗时 {recovered_ms:.0f}ms（疑似死锁）"
    finally:
        if proc.is_alive():  # pragma: no cover
            proc.terminate()
            proc.join(timeout=10.0)


def test_holder_killed_mid_critical_section_lock_file_remains(lock_path):
    """被杀后锁文件**保留**（锁文件永不删除，避免 inode 替换导致互斥静默失效）"""
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    proc = ctx.Process(target=_hold_lock, args=(lock_path, 60.0, ready))
    proc.start()
    try:
        assert ready.get(timeout=_MP_TIMEOUT) is True
        assert os.path.exists(lock_path)
        proc.terminate()
        proc.join(timeout=_MP_TIMEOUT)
        assert os.path.exists(lock_path), "锁文件不应被删除"
        recovered = CrossProcessLock(lock_path, name="parent")
        assert recovered.try_lock() is True
        recovered.release()
    finally:
        if proc.is_alive():  # pragma: no cover
            proc.terminate()
            proc.join(timeout=10.0)


# ════════════════════════════════════════════════════════════
#  4. 诊断与陈旧判定
# ════════════════════════════════════════════════════════════


def test_read_holder_reports_pid(lock_path):
    lock = CrossProcessLock(lock_path, name="diag", holder_info={"db_path": "x.db"})
    assert lock.try_lock()
    try:
        holder = lock.read_holder()
        assert holder.get("pid") == os.getpid()
        assert holder.get("name") == "diag"
        assert holder.get("db_path") == "x.db"
    finally:
        lock.release()


def test_status_snapshot(lock_path):
    lock = CrossProcessLock(lock_path, name="diag")
    assert lock.try_lock()
    try:
        status = lock.status()
        assert status["held_by_self"] is True
        assert status["os_lock_held"] is True
        assert status["path"] == lock.path
    finally:
        lock.release()


def test_is_stale_false_when_no_lockfile(lock_path):
    lock = CrossProcessLock(lock_path, name="diag")
    assert lock.is_stale() is False


def test_is_stale_has_no_side_effect(lock_path):
    """诊断**不得创建**锁文件（诊断不应改变被诊断对象）

    【实现期实测缺陷（watchdog_singleton 同源）】第一版让 ``is_stale()`` 走
    "能否拿到 OS 锁"的探测路径，而该路径会**创建**锁文件 ⇒ 无锁文件时返回
    "锁文件存在且陈旧"的自相矛盾结论。
    """
    lock = CrossProcessLock(lock_path, name="diag")
    lock.is_stale()
    lock.status()
    assert not os.path.exists(lock_path), "诊断路径创建了锁文件（违反无副作用）"


def test_is_stale_true_after_release(lock_path):
    """本进程释放后：锁文件在、无活跃持有者 ⇒ 陈旧（可安全清理残留）"""
    lock = CrossProcessLock(lock_path, name="diag")
    assert lock.try_lock()
    lock.release()
    assert os.path.exists(lock_path)
    assert lock.is_stale() is True


def test_is_stale_false_while_held_by_other_process(lock_path):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    proc = ctx.Process(target=_hold_lock, args=(lock_path, 3.0, ready))
    proc.start()
    try:
        assert ready.get(timeout=_MP_TIMEOUT) is True
        assert CrossProcessLock(lock_path, name="diag").is_stale() is False
    finally:
        proc.join(timeout=_MP_TIMEOUT)
        if proc.is_alive():  # pragma: no cover
            proc.terminate()
            proc.join(timeout=10.0)


def test_lock_path_for_uses_separate_file():
    """锁文件必须与被保护资源**分离**（否则 rename 原子写会让锁落到旧 inode）"""
    assert lock_path_for("/tmp/a.db") == "/tmp/a.db.lock"
    assert lock_path_for("/tmp/a.db") != "/tmp/a.db"


# ════════════════════════════════════════════════════════════
#  5. 可观测计数（否则"加固了但不知道有没有生效"）
# ════════════════════════════════════════════════════════════


def test_metrics_count_acquire_conflict_timeout(lock_path):
    reset_lock_metrics()
    reset_conflict_cooldown()
    with _BackgroundHolder(lock_path):
        # conflict：非阻塞被拒（try_lock 与 acquire(0) 两条路径都要计）
        assert CrossProcessLock(lock_path, name="c").try_lock() is False
        with pytest.raises(LockUnavailable):
            CrossProcessLock(lock_path, name="c2").acquire(0)
        # timeout：有限等待超时
        with pytest.raises(LockTimeout):
            CrossProcessLock(lock_path, name="d").acquire(0.2)

    metrics = lock_metrics()
    assert metrics["acquired"] >= 1
    assert metrics["conflicts"] >= 2, f"非阻塞被拒未计数: {metrics}"
    assert metrics["timeouts"] >= 1, f"超时未计数: {metrics}"
    assert metrics["degraded"] >= 1, "失败未走留痕计数"
    assert metrics["os_lock_acquires"] >= 1


def test_metrics_count_wait(lock_path):
    reset_lock_metrics()
    with _BackgroundHolder(lock_path) as holder:
        holder.release_after(0.2)
        with CrossProcessLock(lock_path, name="waiter").locked(timeout=20.0):
            pass
    metrics = lock_metrics()
    assert metrics["waits"] >= 1, f"真实等待未计数: {metrics}"
    assert metrics["wait_ms_max"] > 0
    assert metrics["wait_ms_avg"] >= 0


def test_metrics_count_reentrant(lock_path):
    reset_lock_metrics()
    lock = CrossProcessLock(lock_path, name="r")
    assert lock.try_lock()
    assert lock.try_lock()
    lock.release()
    lock.release()
    assert lock_metrics()["reentrant"] >= 1


def test_reset_lock_metrics(lock_path):
    lock = CrossProcessLock(lock_path, name="r")
    lock.try_lock()
    lock.release()
    assert lock_metrics()["acquired"] >= 1
    reset_lock_metrics()
    assert lock_metrics()["acquired"] == 0


# ════════════════════════════════════════════════════════════
#  6. 降级留痕（硬约束：不静默）
# ════════════════════════════════════════════════════════════


def test_conflict_and_timeout_are_traced(lock_path):
    """冲突与超时**必须**留痕（注入探针，断言 action + 关键字段）"""
    seen = []
    set_notify_hook(lambda action, detail: seen.append((action, detail)))
    reset_conflict_cooldown()

    with _BackgroundHolder(lock_path):
        assert CrossProcessLock(lock_path, name="c").try_lock() is False
        with pytest.raises(LockTimeout):
            CrossProcessLock(lock_path, name="d").acquire(0.2)

    actions = [a for a, _ in seen]
    assert "lock.conflict" in actions, f"冲突未留痕: {actions}"
    assert "lock.timeout" in actions, f"超时未留痕: {actions}"
    timeout_detail = dict(seen[actions.index("lock.timeout")][1])
    assert timeout_detail.get("lock_path") == os.path.abspath(lock_path)
    assert "holder" in timeout_detail
    assert timeout_detail.get("pid") == os.getpid()


def test_conflict_notify_is_cooldown_deduplicated(lock_path):
    """冲突留痕按 (路径, 进程) 冷却去重——**不静默**但也不放大

    【为什么这条必需】退避重试循环会在几秒内产生上百次冲突；若逐条写审计，
    审计链被冲垮反而掩盖真实问题。口径：**计数不冷却**（每次冲突都进
    ``lock_metrics``），**留痕冷却**（窗口内首次必留）。
    """
    seen = []
    set_notify_hook(lambda action, detail: seen.append((action, detail)))
    reset_conflict_cooldown()
    reset_lock_metrics()

    with _BackgroundHolder(lock_path):
        for _ in range(10):
            assert CrossProcessLock(lock_path, name="c").try_lock() is False

    assert len([a for a, _ in seen if a == "lock.conflict"]) == 1, \
        f"冲突留痕未去重: {seen}"
    assert lock_metrics()["conflicts"] >= 10, "计数不应被冷却影响"


def test_timeout_notify_is_not_deduplicated(lock_path):
    """超时是终止性失败：**每次**都要留痕（冷却只作用于冲突）"""
    seen = []
    set_notify_hook(lambda action, detail: seen.append((action, detail)))
    reset_conflict_cooldown()

    with _BackgroundHolder(lock_path):
        for _ in range(3):
            with pytest.raises(LockTimeout):
                CrossProcessLock(lock_path, name="d").acquire(0.1)

    assert len([a for a, _ in seen if a == "lock.timeout"]) == 3, seen


def test_degradation_is_actually_audited(lock_path, tmp_path):
    """**默认留痕真的落到审计链**（不是"看起来留痕了"）

    【为什么必须有这条（实测缺陷）】默认留痕实现里把审计门面的入口名写错了
    （`get_audit_facade()` 并不存在，真实入口是模块级 `record()`）。因为整段被
    `except Exception` 包着，**所有注入探针的用例都照常通过**——探针只证明
    "钩子被调用"，不证明"真的写进去了"。运行期的实际后果是"锁降级从不留痕"，
    直接违反本任务「锁超时须显式失败 + 留痕」的硬约束（mypy 是唯一露头的地方）。

    本用例恢复**默认钩子**（不注入探针），把审计链换到 tmp 路径，触发一次
    锁冲突，然后断言审计链里查得到 `lock.*` 记录。
    """
    from agent.audit import facade as audit_facade
    from agent.audit.chain import AuditChain, reset_audit_chains

    chain = AuditChain(str(tmp_path / "audit.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_enabled=False, auto_seal=False)
    previous = audit_facade.audit.bind(chain)
    set_notify_hook(None)          # 恢复默认 = 审计链 + 事件层
    reset_conflict_cooldown()
    try:
        with _BackgroundHolder(lock_path):
            got = CrossProcessLock(lock_path, name="audited").try_lock()
        assert got is False, "前置条件：应当发生一次锁冲突"
        chain.flush(timeout=10.0)
        actions = [e.action for e in chain.entries()]
        assert any(a.startswith("lock.") for a in actions), (
            f"锁降级未真正写入审计链（留痕形同虚设）: {actions}")
    finally:
        audit_facade.audit.bind(previous)
        reset_audit_chains()


def test_notify_hook_exception_does_not_break_locking(lock_path):
    """留痕钩子自身抛异常**不得**影响加锁（观测绝不能成为新的故障点）"""
    def boom(action, detail):
        raise RuntimeError("hook down")

    set_notify_hook(boom)
    holder = CrossProcessLock(lock_path, name="holder")
    assert holder.try_lock()
    try:
        # 竞争必须在**另一个线程**发起：同线程是重入语义（见
        # test_reentrant_acquire_by_same_thread_balanced），不会产生冲突
        result = {}

        def contender():
            try:
                CrossProcessLock(lock_path, name="c").acquire(0)
                result["outcome"] = "acquired"
            except LockUnavailable:
                result["outcome"] = "LockUnavailable"

        t = threading.Thread(target=contender)
        t.start()
        t.join(timeout=5.0)
        assert result.get("outcome") == "LockUnavailable"
    finally:
        holder.release()


# ════════════════════════════════════════════════════════════
#  7. 锁文件不可用 → 显式错误
# ════════════════════════════════════════════════════════════


def test_lock_file_error_when_directory_is_a_file(tmp_path):
    """锁文件父路径是普通文件 ⇒ 显式 LockFileError（不是静默降级）"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    lock = CrossProcessLock(str(blocker / "sub" / "a.lock"), name="bad")
    with pytest.raises(LockFileError):
        lock.acquire(0.05)
