"""TASK-S8-02 ``UnifiedTraceStore`` 写路径加固 单元测试

``UnifiedTraceStore`` 是本模块**最后一条**未加固的写路径。本文件逐条锁死加固后的口径：

1. **4 进程并发写**（``spawn``，模块级 worker）：计数守恒（不丢不重）+ 每一行可解析 +
   哈希一致（列 ``trace_id`` 与 JSON 内 ``trace_id`` 也必须一致 → 撕行会暴露）。
2. **有界队列满**：计数 + 限流告警 + 结构化事件留痕，且记录转 ring buffer（**不静默丢**）。
3. **ring buffer 满**：``ring_buffer_dropped`` 递增（原 ``deque(maxlen=)`` 是无声丢弃）。
4. **降级 → 恢复**：``_degraded`` 不再终身粘住；``consecutive_failures`` /
   ``degraded_recoveries`` 区分"抖动"与"真死"。
5. **WAL 真的开了**：用**新连接**读 ``PRAGMA journal_mode``（journal_mode 是 DB 文件
   的持久属性，不是连接级）。
6. **坏行可见**：``skipped_row_count`` + 限流告警。
7. **``flush()`` 不说谎**：``flush()``（在途清零）与 ``flush_durable()`` /
   ``stats()['durable']``（真落盘）两个口径必须能分开。
8. **跨进程锁真的被取过**：外部持锁时 ``lock_bypass_count`` 递增，且**取不到锁也照写**
   （不丢数据）——这条同时证明"锁不是静默点、也不是数据正确性的必要条件"。
9. **连接收尾**：``stop()`` 关闭连接（原实现永不关闭），且 ``stop()`` 之后读接口仍可用。
10. **完整性抽查**：``verify_integrity()`` 是**显式 opt-in**，默认读路径**不**跑。

隔离（硬要求）：所有用例显式传 ``tmp_path`` DB 路径，**绝不触碰** ``agent/data/`` 与
``data/``。写路径降级路径会触发锁原语的默认留痕（审计链）与本模块的事件出口，故用例级
autouse fixture 把事件目录指向 ``tmp_path`` 并把锁原语的留痕钩子换成进程内探针
（S3-02/S3-03 有"测试污染真实 data/ 目录"的既有教训）。
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue as queue_module
import sqlite3
import sys
import time
from typing import Any, Dict, List, Tuple

import pytest

from agent.observability import trace_v2
from agent.observability.trace_v2 import (
    ENV_DEGRADED_RETRY_SEC,
    ENV_QUEUE_MAXSIZE,
    ENV_RING_BUFFER_MAXLEN,
    EVENT_TRACE_OVERFLOW,
    EVENT_TRACE_QUEUE_FULL,
    Request,
    Response,
    Tenancy,
    TraceFacade,
    UnifiedTrace,
    UnifiedTraceStore,
    hash_content,
    redact_then_hash,
)

pytestmark = [pytest.mark.unit]

# spawn 子进程要按模块名重新导入本文件（``multiprocessing`` 会把 ``sys.path`` 一并
# 传下去，正常情况已包含本目录）；显式再保一次，避免 CI 上 importmode 差异导致
# worker 不可 pickle。
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
if _TEST_DIR not in sys.path:  # pragma: no cover - 正常路径已包含
    sys.path.insert(0, _TEST_DIR)

LOGGER_NAME = "agent.observability.trace_v2"


# ════════════════════════════════════════════════════════════
#  fixture / 工具
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_side_effects(tmp_path, monkeypatch):
    """隔离所有**进程外**副作用：事件目录 → tmp_path，锁留痕钩子 → 探针

    【为什么必须】写路径降级时会 (a) 让锁原语通过默认 ``notify_hook`` 写**审计链**、
    (b) 让本模块 ``emit`` 写**事件文件**。不隔离就会写真实的 ``data/audit`` 与
    ``data/events``——本仓库已有 S3-02/S3-03 的同类污染记录。

    Yields:
        锁降级留痕探针（``[(action, detail), ...]``），用于断言"超时确实留痕了"。
    """
    import agent.observability.events as events_mod
    from agent.utils import cross_process_lock as cpl

    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("CP_EVENTS_AUDIT_MIRROR", "0")   # 不镜像进审计链
    # Trace → 审计链的留痕整体关掉：本文件测的是 trace **存储**写路径；不关的话
    # ``TraceFacade.finish()``（``trace.closed``）会去写真实的 data/audit/ 台账。
    monkeypatch.setenv("AUDIT_TRACE_EVENTS", "0")
    # 【结构保证，而不只是"我写对了"】把模块级默认库路径也指到 tmp_path：即便将来
    # 有人在本文件里写了 ``UnifiedTraceStore()``（漏传路径），也**不可能**写到真实的
    # ``agent/data/tool_trace.db``（含其 ``.lock``）。本仓库有 S3-02/S3-03 的污染前科。
    monkeypatch.setattr(trace_v2, "_DEFAULT_DB_PATH",
                        str(tmp_path / "unexpected_default_store.db"))
    events_mod.reset_event_stores()

    notices: List[Tuple[str, Dict[str, Any]]] = []
    cpl.set_notify_hook(lambda action, detail: notices.append((action, detail)))
    cpl.reset_lock_metrics()
    cpl.reset_conflict_cooldown()
    try:
        yield notices
    finally:
        cpl.set_notify_hook(None)
        cpl.reset_lock_metrics()
        cpl.reset_conflict_cooldown()
        events_mod.reset_event_stores()


@pytest.fixture
def open_stores():
    """构造器工厂：用例结束时统一 ``stop()``（``stop`` 是终态，必须收尾）

    即使用例自己**没有**调用 ``stop()``，这里也会兜底收尾——覆盖"用例忘了停"的路径。
    """
    created: List[UnifiedTraceStore] = []

    def _make(db_path: str) -> UnifiedTraceStore:
        store = UnifiedTraceStore(str(db_path))
        created.append(store)
        return store

    yield _make
    for store in created:
        try:
            store.stop(timeout=10.0)
        except Exception as exc:  # noqa: BLE001 收尾失败不影响断言结论
            logging.getLogger(LOGGER_NAME).debug("用例收尾 stop 失败: %s", exc)


def _trace(trace_id: str, *, workspace_id: str = "ws_test",
           capability_id: str = "cp.s8.02.write") -> UnifiedTrace:
    """构造一条最小 UnifiedTrace（trace_id 由调用方给定，便于计数守恒断言）"""
    return UnifiedTrace(
        trace_id=trace_id,
        task_id="task-s8-02",
        capability_id=capability_id,
        tenancy=Tenancy(workspace_id=workspace_id),
    )


def _counter(store: UnifiedTraceStore, name: str) -> int:
    """直接读进程内计数（**避开** ``stats()``/``count()`` 的读扫描副作用）"""
    with store._count_lock:
        return int(getattr(store, name))


def _read_events(tmp_path) -> List[Dict[str, Any]]:
    """读回隔离事件目录里的结构化留痕（"不静默"的落盘证据）"""
    path = tmp_path / "events" / "events.jsonl"
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _event_types(tmp_path) -> List[str]:
    return [str(e.get("type") or "") for e in _read_events(tmp_path)]


# ════════════════════════════════════════════════════════════
#  多进程 worker（**模块级**：spawn 需要可 pickle）
# ════════════════════════════════════════════════════════════


def _mp_writer_worker(db_path: str, worker_idx: int, n_records: int, out: Any) -> None:
    """spawn 子进程写入 worker：写 N 条能力级 Trace + 1 条任务级主 Trace

    只往 ``out``（``multiprocessing`` 队列）里放**朴素 JSON 可序列化**的结果，
    绝不回传 store/facade 之类的 live 对象。
    """
    result: Dict[str, Any] = {
        "worker": worker_idx, "pid": os.getpid(), "records": 0,
        "trace_ids": [], "flushed": False, "durable": False, "stats": {},
        "error": "",
    }
    facade = None
    try:
        facade = TraceFacade(db_path)
        facade.start(task_id=f"task-mp-{worker_idx}", workspace_id="ws_mp")
        for i in range(n_records):
            trace = facade.record(
                "cp.mp.write",
                args={"worker": worker_idx, "i": i, "path": f"/tmp/mp{i}.txt"},
                output={"ok": True, "i": i},
            )
            result["trace_ids"].append(trace.trace_id)
        main_trace = facade.finish(status="success", output={"ok": True})
        if main_trace is not None:
            result["trace_ids"].append(main_trace.trace_id)
        result["records"] = len(result["trace_ids"])
        result["flushed"] = bool(facade.flush(timeout=120.0))
        result["durable"] = bool(facade.flush_durable(timeout=120.0))
        stats = facade._store.stats()
        result["stats"] = {
            key: stats.get(key) for key in (
                "total", "durable", "uncommitted_count", "degraded",
                "degraded_reason", "batch_failure_count", "queue_overflow_count",
                "ring_buffer_dropped", "skipped_row_count", "read_failure_count",
                "lock_write_count", "lock_bypass_count", "journal_mode",
            )
        }
    except Exception as exc:  # noqa: BLE001 子进程错误回传，父进程据此判定失败原因
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if facade is not None:
            try:
                facade._store.stop(timeout=30.0)
            except Exception:  # noqa: BLE001
                pass
    out.put(result)


# ════════════════════════════════════════════════════════════
#  1. 多进程并发写：计数守恒 + 无腐坏
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(180)
def test_four_process_concurrent_writes_no_loss_no_corruption(tmp_path):
    """4 进程 × 250 条并发写同一 DB：**一条不丢、一条不重、一行不坏**

    每条记录 2 个校验维度：(a) ``trace_id`` 计数守恒（子进程自报的 id 集合 == 库里
    的 id 集合）；(b) 哈希一致（``args_hash == hash_content(args_redacted)``）。
    (c) 列 ``trace_id`` 必须与 payload 内的 ``trace_id`` 相等——多进程撕行/错位会
    立刻在这里暴露。
    """
    proc_count = 4
    per_proc = 250
    db_path = str(tmp_path / "mp_hardening.db")

    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = [
        ctx.Process(target=_mp_writer_worker,
                    args=(db_path, idx, per_proc, out),
                    name=f"trace-mp-writer-{idx}")
        for idx in range(proc_count)
    ]
    started = time.perf_counter()
    for proc in procs:
        proc.start()

    results: List[Dict[str, Any]] = []
    for _ in procs:
        try:
            results.append(out.get(timeout=180))
        except queue_module.Empty:
            results.append({"worker": -1, "error": "等待子进程结果超时",
                            "trace_ids": [], "stats": {}})
    for proc in procs:
        proc.join(timeout=60)
    elapsed = time.perf_counter() - started

    # ── 子进程侧：必须全部成功且**真落盘** ──
    assert [p.exitcode for p in procs] == [0] * proc_count, \
        f"子进程退出码异常: {[p.exitcode for p in procs]}"
    errors = [r for r in results if r.get("error")]
    assert not errors, f"子进程报错: {errors}"
    for r in results:
        assert r["flushed"] is True, r
        assert r["durable"] is True, r                 # flush() 之外还要"真落盘"口径
        assert r["stats"]["uncommitted_count"] == 0, r
        assert r["stats"]["degraded"] is False, r
        assert r["stats"]["degraded_reason"] == "", r
        assert r["stats"]["batch_failure_count"] == 0, r
        assert r["stats"]["journal_mode"].lower() == "wal", r

    reported_ids: List[str] = []
    for r in results:
        assert r["records"] == per_proc + 1, r
        reported_ids.extend(r["trace_ids"])
    assert len(reported_ids) == proc_count * (per_proc + 1)
    assert len(set(reported_ids)) == len(reported_ids), "子进程自报的 trace_id 出现重复"

    # ── 父进程直连库读（不经 store，避免 writer 线程干扰）──
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT trace_id, payload FROM unified_traces").fetchall()
    finally:
        conn.close()
    assert len(rows) == len(reported_ids), \
        f"计数不守恒：库内 {len(rows)} 行 vs 写入 {len(reported_ids)} 条（耗时 {elapsed:.1f}s）"

    seen = set()
    for row in rows:
        payload = json.loads(row["payload"])           # 每一行都必须可解析
        assert payload["trace_id"] == row["trace_id"], "列与 payload 的 trace_id 不一致（疑似撕行/错位）"
        trace = UnifiedTrace.from_dict(payload)
        if trace.request.args_hash:
            assert trace.request.args_hash == hash_content(trace.request.args_redacted), \
                f"args 哈希不一致: {row['trace_id']}"
        if trace.response.output_hash:
            assert trace.response.output_hash == hash_content(trace.response.output_redacted), \
                f"output 哈希不一致: {row['trace_id']}"
        assert trace.tenancy.workspace_id == "ws_mp"
        seen.add(row["trace_id"])
    assert seen == set(reported_ids), "库内 trace_id 集合与写入集合不一致（丢失或凭空出现）"
    print(f"[S8-02][mp] procs={proc_count} per_proc={per_proc} "
          f"reported={len(reported_ids)} rows={len(rows)} "
          f"exitcodes={[p.exitcode for p in procs]} elapsed={elapsed:.2f}s")

    # ── 读接口口径：能读到全部，且没有坏行 ──
    store = UnifiedTraceStore(db_path)
    try:
        stats = store.stats()
        assert stats["total"] == len(reported_ids)
        assert stats["skipped_row_count"] == 0
        assert stats["read_failure_count"] == 0
        assert store.query(capability_id="cp.mp.write") != []
    finally:
        store.stop(timeout=30.0)


# ════════════════════════════════════════════════════════════
#  2. 有界队列满：计数 + 告警 + 事件留痕，且不静默丢
# ════════════════════════════════════════════════════════════


def test_bounded_queue_full_is_counted_warned_and_not_silently_dropped(
        tmp_path, monkeypatch, caplog, open_stores):
    """队列满必须**显式降级**（返回 False + 计数 + 告警 + 事件），不是静默丢

    【为什么先 ``stop()``】``stop()`` 是终态：writer 退出后队列不再被排空，
    ``queue.Full`` 才能被**确定性**触发（否则要跟 writer 抢时序）。
    """
    monkeypatch.setenv(ENV_QUEUE_MAXSIZE, "2")
    store = open_stores(str(tmp_path / "queue_full.db"))
    assert store.stop(timeout=5.0) is True

    traces = [_trace(f"{i:016d}") for i in range(3)]
    assert store.record(traces[0]) is True
    assert store.record(traces[1]) is True
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert store.record(traces[2]) is False      # 显式降级（不再"静默"）

    stats = store.stats()
    assert stats["queue_maxsize"] == 2
    assert stats["queue_overflow_count"] == 1        # 计数（不是静默丢）
    assert stats["uncommitted_count"] == 1
    assert stats["ring_buffer_pending"] == 1         # 记录进了 ring buffer（可读可见）
    assert stats["durable"] is False                 # 诚实口径：没有全部落盘
    assert store.count() == 1                        # 溢出那条仍可读（不丢观测）
    assert any("队列已满" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
    assert EVENT_TRACE_QUEUE_FULL in _event_types(tmp_path)


# ════════════════════════════════════════════════════════════
#  3. ring buffer 满：丢弃可见（计数 + 告警 + 事件）
# ════════════════════════════════════════════════════════════


def test_ring_buffer_overflow_drops_are_counted_and_traced(
        tmp_path, monkeypatch, caplog, open_stores):
    """ring buffer 满 ⇒ ``ring_buffer_dropped`` 递增（原 ``deque(maxlen=)`` 无声丢弃）

    用"缺 workspace_id"这条**确定性**降级路径把记录塞进 ring buffer（P7.1-19 策略性
    降级，另有 ``workspace_degraded`` 计数，不计入"写路径未落盘"口径）。
    """
    monkeypatch.setenv(ENV_RING_BUFFER_MAXLEN, "3")
    store = open_stores(str(tmp_path / "ring_overflow.db"))
    traces = [_trace(f"{i:016d}", workspace_id="") for i in range(5)]
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        for trace in traces:
            assert store.record(trace) is False

    stats = store.stats()
    assert stats["ring_buffer_maxlen"] == 3
    assert stats["ring_buffer_pending"] == 3
    assert stats["ring_buffer_dropped"] == 2         # ← 缺陷 #2 的核心断言
    assert stats["workspace_degraded"] == 5
    assert stats["uncommitted_count"] == 0           # 策略性降级不计入"写路径未落盘"
    kept = {t.trace_id for t in store.query()}
    assert kept == {traces[i].trace_id for i in (2, 3, 4)}, "应为丢最旧、保最新"
    assert any("ring buffer 已满" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
    assert EVENT_TRACE_OVERFLOW in _event_types(tmp_path)


# ════════════════════════════════════════════════════════════
#  4. 降级可恢复（不再终身粘住）
# ════════════════════════════════════════════════════════════


def test_degraded_state_is_recoverable_and_counted(tmp_path, monkeypatch, open_stores):
    """一次失败 → 降级；下一次成功 → **清除** ``_degraded`` 并计 ``degraded_recoveries``

    【为什么把退避设 0】默认 ``CP_TRACE_DEGRADED_RETRY_SEC=2.0`` 会让"恢复"必须等
    窗口过期；用例设 0 表示"每批都重试"，从而不必 sleep（退避本身另有用例覆盖）。
    """
    monkeypatch.setenv(ENV_DEGRADED_RETRY_SEC, "0")
    store = open_stores(str(tmp_path / "recovery.db"))

    real_get_conn = store._get_conn
    state = {"calls": 0}

    def flaky_get_conn():
        state["calls"] += 1
        if state["calls"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return real_get_conn()

    monkeypatch.setattr(store, "_get_conn", flaky_get_conn)

    # ── 第一步：一次瞬时失败 ⇒ 降级 ──
    assert store.record(_trace("a" * 16)) is True
    assert store.flush(timeout=20.0) is True
    stats = store.stats()
    assert stats["degraded"] is True
    assert stats["degraded_reason"].startswith("OperationalError")
    assert stats["consecutive_failures"] == 1
    assert stats["batch_failure_count"] == 1
    assert stats["degraded_enter_count"] == 1
    assert stats["degraded_recoveries"] == 0
    assert stats["uncommitted_count"] == 1
    assert stats["durable"] is False
    # flush() True（在途清零）但 flush_durable() False（没有真落盘）——两个口径分开
    assert store.flush_durable(timeout=10.0) is False
    assert trace_v2.EVENT_TRACE_DEGRADED in _event_types(tmp_path)   # 降级有落盘留痕

    # ── 第二步：下一次成功 ⇒ **恢复**（原实现会在这里永久卡在降级态）──
    assert store.record(_trace("b" * 16)) is True
    assert store.flush(timeout=20.0) is True
    stats = store.stats()
    assert stats["degraded"] is False, "降级必须可恢复（原实现是终身粘住）"
    assert stats["degraded_reason"] == ""
    assert stats["consecutive_failures"] == 0
    assert stats["degraded_recoveries"] == 1
    assert stats["degraded_enter_count"] == 1
    assert stats["durable_count"] == 1
    assert stats["last_recovery_ts"] > 0
    # 第一条仍在 ring buffer：**不假装**它落盘了（uncommitted 是单调累计口径）
    assert stats["uncommitted_count"] == 1
    assert stats["durable"] is False
    assert store.count() == 2                        # 两条都可见（一条库内、一条内存）


def test_degraded_retry_is_backed_off_to_avoid_hammering_broken_db(
        tmp_path, monkeypatch, open_stores):
    """降级态下不"每批都砸库"：退避窗口内只记 ``degraded_retry_skips``

    默认值断言放在这里（``DEFAULT_DEGRADED_RETRY_SEC``），实际用 60s 让窗口**确定**
    覆盖整个用例（避免 CI 负载下 writer 1s 轮询把窗口撞过去，变成 flaky）。
    """
    assert trace_v2.DEFAULT_DEGRADED_RETRY_SEC == 2.0
    monkeypatch.setenv(ENV_DEGRADED_RETRY_SEC, "60")
    store = open_stores(str(tmp_path / "backoff.db"))

    def always_fail():
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(store, "_get_conn", always_fail)

    assert store.record(_trace("a" * 16)) is True
    assert store.flush(timeout=20.0) is True
    assert _counter(store, "_batch_failure_count") == 1

    assert store.record(_trace("b" * 16)) is True
    assert store.flush(timeout=20.0) is True
    assert _counter(store, "_batch_failure_count") == 1, "退避窗口内不该再碰坏库"
    assert _counter(store, "_degraded_retry_skips") >= 1
    assert _counter(store, "_uncommitted_count") == 2, "两条都要留在 ring buffer（一条不丢）"


# ════════════════════════════════════════════════════════════
#  5. WAL 真的开了
# ════════════════════════════════════════════════════════════


def test_wal_journal_mode_is_enabled_on_trace_db(tmp_path, monkeypatch, open_stores):
    """``PRAGMA journal_mode`` == ``wal``（**新连接**验证：它是 DB 文件的持久属性）"""
    db_path = str(tmp_path / "wal.db")
    monkeypatch.setenv(trace_v2.ENV_WAL_AUTOCHECKPOINT, "3000")
    store = open_stores(db_path)
    assert store.record(_trace("a" * 16)) is True
    assert store.flush(timeout=20.0) is True

    assert store.stats()["journal_mode"].lower() == "wal"
    # 新连接（不经 store）：journal_mode 随库持久化，必须仍是 wal
    conn = sqlite3.connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"
    # 连接级 PRAGMA（在本模块自己的连接上验）：busy_timeout 必须保留"会等待"，
    # synchronous 必须取保守值 FULL(2)，wal_autocheckpoint 必须听 env 配置。
    store_conn = store._get_conn()
    busy = int(store_conn.execute("PRAGMA busy_timeout").fetchone()[0])
    sync = int(store_conn.execute("PRAGMA synchronous").fetchone()[0])
    chk = int(store_conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0])
    assert busy == 5000, f"busy_timeout 应保留 5000ms（会等待），实测 {busy}"
    assert sync == 2, f"synchronous 应为 FULL(2)，实测 {sync}"
    assert chk == 3000, f"wal_autocheckpoint 应听 env，实测 {chk}"


# ════════════════════════════════════════════════════════════
#  6. 坏行可见
# ════════════════════════════════════════════════════════════


def test_corrupt_row_is_counted_and_warned(tmp_path, caplog, open_stores):
    """不可解析的 ``payload`` 被跳过时**必须计数 + 告警**（原实现静默 continue）"""
    store = open_stores(str(tmp_path / "corrupt.db"))
    assert store.record(_trace("a" * 16)) is True
    assert store.flush(timeout=20.0) is True
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO unified_traces "
        "(trace_id, task_id, capability_id, started_at, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        ("bad-row", "bad-row", "bad-row", 1.0, "{not-json"))
    conn.commit()

    base = _counter(store, "_skipped_row_count")
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert store.count() == 1                    # 坏行被跳过，不抛异常
    assert _counter(store, "_skipped_row_count") == base + 1
    assert store.stats()["skipped_row_count"] >= base + 1
    assert any("不可解析行" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


# ════════════════════════════════════════════════════════════
#  7. flush() 不说谎
# ════════════════════════════════════════════════════════════


def test_flush_contract_handled_vs_truly_durable(tmp_path, monkeypatch, open_stores):
    """``flush()``（在途清零）与 ``flush_durable()`` / ``stats()['durable']``（真落盘）分开

    选定契约：``flush() is True`` ⇔ 已入队记录都被 writer **处理**（提交**或**显式
    降级）；真正的落盘口径由 ``flush_durable()`` / ``stats()['durable']`` 回答。
    """
    monkeypatch.setenv(ENV_DEGRADED_RETRY_SEC, "0")
    store = open_stores(str(tmp_path / "flush_contract.db"))

    assert store.record(_trace("a" * 16)) is True
    assert store.flush(timeout=20.0) is True
    assert store.flush_durable(timeout=20.0) is True
    stats = store.stats()
    assert stats["handled_count"] == stats["durable_count"] == 1
    assert stats["durable"] is True
    assert store.count() == 1

    # 库坏掉之后：handled 仍推进（记录被**显式**降级），但 durable 必须说真话
    def boom():
        raise sqlite3.OperationalError("no space left on device")

    monkeypatch.setattr(store, "_get_conn", boom)
    assert store.record(_trace("b" * 16)) is True
    assert store.flush(timeout=20.0) is True         # True = 在途清零（**不是**落盘）
    assert store.flush_durable(timeout=20.0) is False
    stats = store.stats()
    assert stats["durable"] is False
    assert stats["handled_count"] == 2
    assert stats["durable_count"] == 1
    assert stats["uncommitted_count"] == 1
    assert stats["ring_buffer_pending"] == 1


def test_flush_guarantees_durability_even_without_stop(tmp_path):
    """**不调用 ``stop()``** 时，``flush()``+``flush_durable()`` 也必须已真落盘

    writer 是 daemon 线程：不能指望"进程退出"来兜底残留队列，故本用例用一个**独立
    连接的新 store** 去读——只有真提交进 SQLite 才可能读到。
    """
    db_path = str(tmp_path / "no_stop.db")
    writer = UnifiedTraceStore(db_path)
    try:
        for i in range(5):
            assert writer.record(_trace(f"{i:016d}")) is True
        assert writer.flush(timeout=20.0) is True
        assert writer.flush_durable(timeout=20.0) is True
        reader = UnifiedTraceStore(db_path)
        try:
            assert reader.count() == 5
            assert reader.stats()["durable_count"] == 0   # 读侧没写过，durable_count 为 0
        finally:
            reader.stop(timeout=10.0)
    finally:
        writer.stop(timeout=10.0)


# ════════════════════════════════════════════════════════════
#  8. 跨进程锁真的被取过，且取不到锁**不丢数据**
# ════════════════════════════════════════════════════════════


def test_write_path_takes_cross_process_lock_and_counts_bypass(
        tmp_path, monkeypatch, open_stores, _isolate_side_effects):
    """外部持锁时：``lock_bypass_count`` 递增（证明真的去要过锁）且**照写成功**

    这条同时锁死两件事：
    - 锁确实在写路径上（否则不会被拒）；
    - 取不到锁**不是**数据风险（SQLite 自身串行化写者 ⇒ 仍照写、仍落盘），
      锁买的是"排队确定性 + 降级可见性"，不是正确性（不夸大）。
    """
    from agent.utils.cross_process_lock import CrossProcessLock

    db_path = str(tmp_path / "locked.db")
    monkeypatch.setenv(trace_v2.ENV_LOCK_TIMEOUT_SEC, "0.05")
    store = open_stores(db_path)

    stats = store.stats()
    assert stats["lock_enabled"] is True
    assert stats["lock_path"] == db_path + ".lock"    # **独立**锁文件（不是 DB 本体）
    assert os.path.exists(stats["lock_path"])
    assert stats["lock_timeout_sec"] == pytest.approx(0.05)

    blocker = CrossProcessLock(stats["lock_path"], name="s802-test-blocker")
    assert blocker.try_lock() is True
    try:
        assert store.record(_trace("a" * 16)) is True
        assert store.flush(timeout=20.0) is True
        stats = store.stats()
        assert stats["lock_bypass_count"] >= 1        # 真的被拒过 ⇒ 锁在写路径上
        assert stats["durable_count"] == 1            # 且**取不到锁也写成功了**
        assert stats["durable"] is True
    finally:
        blocker.release()

    notices = _isolate_side_effects
    assert any(str(action).startswith("lock.") for action, _ in notices), \
        f"锁降级必须留痕（探针未收到）: {notices}"


# ════════════════════════════════════════════════════════════
#  9. 连接收尾
# ════════════════════════════════════════════════════════════


def test_stop_closes_connections_and_reads_still_work(tmp_path, open_stores):
    """``stop()`` 关闭全部连接（原实现永不关闭），且读接口在 stop 后**自动重开**"""
    store = open_stores(str(tmp_path / "close_conn.db"))
    conn = store._get_conn()
    assert store.record(_trace("a" * 16)) is True
    assert store.flush(timeout=20.0) is True
    assert store.stop(timeout=10.0) is True
    assert store._conns == {}, "stop() 之后不应残留登记在册的连接"

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")

    assert store.count() == 1                        # 读接口自动重开连接
    assert store.stats()["skipped_row_count"] == 0


# ════════════════════════════════════════════════════════════
#  10. 完整性抽查：显式 opt-in，默认读路径不跑
# ════════════════════════════════════════════════════════════


def test_verify_integrity_is_opt_in_and_detects_tampering(tmp_path, open_stores):
    """``verify_integrity()`` 能检出"内容被改但哈希没改"；且默认读路径**不**跑它"""
    store = open_stores(str(tmp_path / "integrity.db"))
    args_red, args_hash = redact_then_hash({"path": "/tmp/a.txt"})
    out_red, out_hash = redact_then_hash({"ok": True})
    trace = UnifiedTrace(
        trace_id="a" * 16, task_id="task-s8-02", capability_id="cp.s8.02.write",
        tenancy=Tenancy(workspace_id="ws_test"),
        request=Request(args_redacted=args_red, args_hash=args_hash),
        response=Response(output_redacted=out_red, output_hash=out_hash),
    )
    assert store.record(trace) is True
    assert store.flush(timeout=20.0) is True

    # 默认读路径**不**做校验（避免把读成本成倍放大）
    checked_before = _counter(store, "_integrity_checked")
    assert store.count() == 1
    assert store.query(capability_id="cp.s8.02.write") != []
    assert _counter(store, "_integrity_checked") == checked_before

    report = store.verify_integrity()
    assert report["checked"] == 2 and report["mismatch"] == 0
    assert report["skipped_rows"] == 0
    assert store.stats()["integrity_checked"] == 2

    # 篡改 payload（保留原哈希）⇒ 显式抽查必须发现
    conn = store._get_conn()
    row = conn.execute(
        "SELECT payload FROM unified_traces WHERE trace_id = ?",
        ("a" * 16,)).fetchone()
    tampered = json.loads(row["payload"])
    tampered["args_redacted"] = {"path": "/tmp/TAMPERED.txt"}
    conn.execute("UPDATE unified_traces SET payload = ? WHERE trace_id = ?",
                 (json.dumps(tampered, ensure_ascii=False), "a" * 16))
    conn.commit()

    report = store.verify_integrity()
    assert report["mismatch"] == 1, report
    assert report["suspicious"][0]["field"] == "args"
    assert report["suspicious"][0]["trace_id"] == "a" * 16
    assert store.stats()["integrity_mismatch"] == 1


# ════════════════════════════════════════════════════════════
#  11. 配置默认值保守 + 坏配置不打断写入
# ════════════════════════════════════════════════════════════


def test_env_defaults_are_conservative_and_bad_values_fall_back(monkeypatch, tmp_path):
    """默认值保守（开锁 / 有限等待 / 不静默丢），非法 env **不抛**只回落默认"""
    for name in (trace_v2.ENV_LOCK_ENABLED, trace_v2.ENV_LOCK_TIMEOUT_SEC,
                 trace_v2.ENV_QUEUE_MAXSIZE, trace_v2.ENV_RING_BUFFER_MAXLEN,
                 trace_v2.ENV_DEGRADED_RETRY_SEC, trace_v2.ENV_WAL_AUTOCHECKPOINT,
                 trace_v2.ENV_CORRUPT_LOG_EVERY):
        monkeypatch.delenv(name, raising=False)

    assert trace_v2._env_flag(trace_v2.ENV_LOCK_ENABLED) is True
    assert trace_v2._env_float(trace_v2.ENV_LOCK_TIMEOUT_SEC,
                               trace_v2.DEFAULT_LOCK_TIMEOUT_SEC) == 2.0
    assert trace_v2._env_int(trace_v2.ENV_QUEUE_MAXSIZE,
                             trace_v2.DEFAULT_QUEUE_MAXSIZE) == 20000
    assert trace_v2._env_int(trace_v2.ENV_RING_BUFFER_MAXLEN,
                             trace_v2.RING_BUFFER_MAXLEN) == 1000
    assert trace_v2._env_int(trace_v2.ENV_WAL_AUTOCHECKPOINT,
                             trace_v2.DEFAULT_WAL_AUTOCHECKPOINT) == 1000

    monkeypatch.setenv(trace_v2.ENV_QUEUE_MAXSIZE, "not-an-int")
    monkeypatch.setenv(trace_v2.ENV_LOCK_TIMEOUT_SEC, "")
    monkeypatch.setenv(trace_v2.ENV_LOCK_ENABLED, "0")
    store = UnifiedTraceStore(str(tmp_path / "bad_env.db"))
    try:
        assert store._queue_maxsize == trace_v2.DEFAULT_QUEUE_MAXSIZE
        assert store._lock_timeout == trace_v2.DEFAULT_LOCK_TIMEOUT_SEC
        assert store._write_guard is None            # 显式关锁
        assert store.record(_trace("a" * 16)) is True
        assert store.flush(timeout=20.0) is True     # 关锁不影响写入
        assert store.stats()["durable_count"] == 1
    finally:
        store.stop(timeout=10.0)
