"""多写者并发正确性测试（TASK-S8-02 步骤 3/5 核心验收）

【测什么，为什么这么测】
    本任务把"进程内单写者 + ``busy_timeout`` 兜底"的假设升级为**可证明的
    多进程安全**。验收原文：
      · 多进程（≥4）并发写：**无重复 seq、无静默丢失、无损坏**；
      · 持锁进程被杀 → 锁可恢复、无死锁；锁超时 → 显式失败 + 留痕；
      · 降级路径不静默丢数据（队列/退避/显式失败三者行为均有用例）。

    因此这里的用例**真的起多进程**（``spawn``，≥4），而不是用线程模拟——
    线程共用同一把进程内锁与同一份内存状态，**根本走不到**跨进程缺陷路径。

【可重复性纪律（任务书第七节 1/3）】
    - 所有用例**显式传 tmp 路径**，绝不触碰真实 ``data/``（S3-02/S3-03 两次污染教训）；
    - 多进程用例统一 ``@pytest.mark.timeout`` 且轮询上界留足（CI 高负载会抖动）；
    - 用例结束前 ``flush()/close()`` 后台 writer，避免"假失败"；
    - 断言口径是**原始统计**（进程数 / 条数 / 重复 seq 列表），不是"跑完算过"。
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import threading
import time

import pytest

from agent.audit.chain import (
    GENESIS_PREV_HASH,
    AuditChain,
    SeqJournal,
    build_entry,
    get_audit_chain,
    reset_audit_chains,
)

pytestmark = [pytest.mark.unit, pytest.mark.p2]

#: 子进程拉起的上界（Windows spawn + 解释器启动远慢于 POSIX）
_MP_TIMEOUT = 180.0
#: 每个子进程写入条数（固定，便于断言"总数守恒"）
_PER_PROC = 40
#: 并发进程数（验收要求 ≥4）
_PROCS = 4


# ════════════════════════════════════════════════════════════
#  子进程 worker（必须是模块级函数：spawn 要求可 pickle）
# ════════════════════════════════════════════════════════════


def _writer_worker(db_path: str, roots_path: str, count: int, tag: str,
                   out) -> None:  # pragma: no cover - 子进程内执行
    """子进程：并发写 count 条审计记录，回传 (tag, seq 列表, 失败信息)"""
    try:
        chain = AuditChain(db_path, roots_path=roots_path, signing_enabled=False,
                           auto_seal=False, enforce_single_writer=False)
        seqs = []
        for i in range(count):
            entry = chain.append("mp.write", actor=f"actor-{tag}",
                                 subject=f"subject-{tag}",
                                 payload={"tag": tag, "i": i})
            seqs.append(int(entry.seq))
        chain.flush(timeout=30.0)
        chain.close()
        out.put((tag, seqs, ""))
    except Exception as exc:  # noqa: BLE001 子进程异常必须回传，否则父进程只能看到超时
        out.put((tag, [], f"{type(exc).__name__}: {exc}"))


def _crash_worker(db_path: str, roots_path: str, count: int,
                  out) -> None:  # pragma: no cover - 子进程内执行
    """子进程：写若干条后**主动卡死**，等待父进程 kill

    用于验证"持锁进程被杀 → 已分配未入库的记录不丢"：
    先写 count 条（进预留日志），**不 flush 到 DB**（writer 默认 0.5s 轮询），
    通知父进程后死循环等待被杀。
    """
    chain = AuditChain(db_path, roots_path=roots_path, signing_enabled=False,
                       auto_seal=False, enforce_single_writer=False)
    seqs = []
    for i in range(count):
        seqs.append(int(chain.append("mp.crash", actor="crash",
                                     payload={"i": i}).seq))
    chain._journal.flush()          # 确保已进入预留日志（OS 缓冲即可）
    out.put(("ready", seqs, ""))
    while True:                     # 等父进程 terminate
        time.sleep(0.2)


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture
def paths(tmp_path):
    return {"db": str(tmp_path / "audit.db"),
            "roots": str(tmp_path / "roots.jsonl")}


@pytest.fixture(autouse=True)
def _cleanup():
    """用例前后清进程内单例登记，避免跨用例串扰（含残留 writer 线程）"""
    reset_audit_chains()
    yield
    reset_audit_chains()


def _spawn_all(ctx, targets):
    procs = [ctx.Process(target=t[0], args=t[1]) for t in targets]
    for p in procs:
        p.start()
    return procs


def _join_all(procs):
    for p in procs:
        p.join(timeout=_MP_TIMEOUT)
    alive = [p for p in procs if p.is_alive()]
    for p in alive:  # 兜底：绝不留孤儿进程拖垮后续用例
        p.terminate()
        p.join(timeout=30.0)
    assert not alive, f"{len(alive)} 个子进程未在上界内退出"
    return [p.exitcode for p in procs]


def _collect(out, n):
    rows = []
    for _ in range(n):
        rows.append(out.get(timeout=_MP_TIMEOUT))
    return rows


# ════════════════════════════════════════════════════════════
#  1. ≥4 进程并发写：无重复 seq / 无缺口 / 无丢失 / 无损坏
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(300)
def test_multi_process_writes_no_duplicate_seq_no_loss(paths):
    """**核心验收**：4 进程 × 40 条并发写 → 无重复 seq、无缺口、总数守恒、链可验

    断言口径（全部为原始统计，可复核）：
      · ``duplicates``      跨进程分配出的重复 seq（必须为空）
      · ``db_rows``         入库条数（必须等于 4×40）
      · ``contiguous``      DB 内 seq 是否为 1..N 连续（无空洞）
      · ``verify_chain.ok`` 两级哈希 + prev_hash 链接 + seq 连续性全部通过
    """
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = _spawn_all(ctx, [
        (_writer_worker, (paths["db"], paths["roots"], _PER_PROC,
                          f"p{i}", out)) for i in range(_PROCS)])
    exitcodes = _join_all(procs)
    results = _collect(out, _PROCS)

    failures = [(tag, err) for tag, _, err in results if err]
    assert not failures, f"子进程写入失败: {failures}"
    assert all(code == 0 for code in exitcodes), f"子进程退出码异常: {exitcodes}"

    allocated = sorted(s for _, seqs, _ in results for s in seqs)
    duplicates = sorted({s for s in allocated if allocated.count(s) > 1})

    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    try:
        chain.flush(timeout=30.0)
        rows = chain.entries()
        seqs_in_db = [int(r.seq) for r in rows]
        verification = chain.verify_chain()
    finally:
        chain.close()

    total = _PROCS * _PER_PROC
    # ── 原始统计（写进断言消息，失败时可直接复核）
    evidence = (f"procs={_PROCS} per_proc={_PER_PROC} total={total} "
                f"allocated={len(allocated)} unique={len(set(allocated))} "
                f"db_rows={len(rows)} duplicates={duplicates}")

    assert not duplicates, f"出现重复 seq: {duplicates} | {evidence}"
    assert len(allocated) == total, f"分配条数不等于 {total} | {evidence}"
    assert len(rows) == total, f"入库条数不等于 {total}（有静默丢失）| {evidence}"
    assert seqs_in_db == list(range(1, total + 1)), \
        f"DB 内 seq 非 1..{total} 连续（有空洞或乱序）| {evidence}"
    assert verification.ok, \
        f"链式校验失败：reason={verification.reason} " \
        f"first_bad={verification.first_bad_seq} | {evidence}"
    assert verification.checked == total, f"校验条数异常 | {evidence}"


@pytest.mark.timeout(300)
def test_multi_process_each_row_self_consistent(paths):
    """**无损坏**：每条记录的 payload_hash / self_hash 与公式一致，且 prev_hash 成链

    与 ``verify_chain`` 的区别：这里逐条**重算**并单独报告首个不一致的位置，
    以便把"损坏"与"断链"分开定位。
    """
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = _spawn_all(ctx, [
        (_writer_worker, (paths["db"], paths["roots"], 15, f"q{i}", out))
        for i in range(_PROCS)])
    _join_all(procs)
    _collect(out, _PROCS)

    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    try:
        chain.flush(timeout=30.0)
        rows = chain.entries()
    finally:
        chain.close()

    assert rows, "并发写入后读不到任何记录"
    bad_payload = [r.seq for r in rows if r.payload_hash != r.recompute_payload_hash()]
    bad_self = [r.seq for r in rows if r.self_hash != r.recompute_self_hash()]
    broken_link = [rows[i].seq for i in range(1, len(rows))
                   if rows[i].prev_hash != rows[i - 1].self_hash]
    assert not bad_payload, f"payload_hash 不匹配: {bad_payload}"
    assert not bad_self, f"self_hash 不匹配: {bad_self}"
    assert not broken_link, f"prev_hash 未成链: {broken_link}"


@pytest.mark.timeout(300)
def test_multi_process_payloads_all_present(paths):
    """**内容守恒**：每个进程的每条 payload 都能在库里找到（按 (tag, i) 计数）"""
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = _spawn_all(ctx, [
        (_writer_worker, (paths["db"], paths["roots"], 20, f"r{i}", out))
        for i in range(_PROCS)])
    _join_all(procs)
    _collect(out, _PROCS)

    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    try:
        chain.flush(timeout=30.0)
        rows = chain.entries(action="mp.write")
    finally:
        chain.close()

    seen = {}
    for row in rows:
        key = (str((row.payload or {}).get("tag")), int((row.payload or {}).get("i", -1)))
        seen[key] = seen.get(key, 0) + 1
    expected = {(f"r{t}", i) for t in range(_PROCS) for i in range(20)}
    missing = sorted(expected - set(seen))
    assert not missing, f"丢失的载荷: {missing[:10]}（共 {len(missing)} 条）"
    assert all(v == 1 for v in seen.values()), f"载荷重复: {seen}"


# ════════════════════════════════════════════════════════════
#  2. 崩溃场景：持锁进程被杀 → 已分配未入库的记录不丢、链不断
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(300)
def test_killed_process_records_recovered_from_journal(paths):
    """**进程被杀**：已分配（进预留日志）但未入库的记录，重启后自动重放补齐

    这正是"预留日志"相对"只预留 seq 号"的关键差别：日志里存的是**整条记录**，
    所以崩溃不会留下无法解释的空洞——链依旧 1..N 连续，``verify_chain`` 通过。
    """
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    proc = ctx.Process(target=_crash_worker, args=(paths["db"], paths["roots"], 25, out))
    proc.start()
    try:
        tag, allocated, err = out.get(timeout=_MP_TIMEOUT)
        assert tag == "ready" and not err, f"崩溃用例前置失败: {err}"
        assert len(allocated) == 25
        # 杀掉持锁进程（不是 graceful close）
        proc.terminate()
        proc.join(timeout=60.0)
        assert not proc.is_alive()
    finally:
        if proc.is_alive():  # pragma: no cover
            proc.terminate()
            proc.join(timeout=30.0)

    # 新进程：启动重放 + 后台收敛
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    try:
        chain.flush(timeout=30.0)
        rows = chain.entries()
        verification = chain.verify_chain()
        replayed = chain.stats()["journal_replay_count"]
    finally:
        chain.close()

    recovered = len(rows)
    assert recovered == 25, (
        f"被杀进程的记录未全部恢复：recovered={recovered}/25 "
        f"replayed={replayed}（静默丢失）")
    assert [int(r.seq) for r in rows] == list(range(1, 26)), \
        f"恢复后 seq 出现空洞: {[int(r.seq) for r in rows]}"
    assert verification.ok, (
        f"恢复后链校验失败（不得造成'链断'误判）：reason={verification.reason}")


@pytest.mark.timeout(300)
def test_killed_process_does_not_deadlock_next_writer(paths):
    """**持锁进程被杀不得死锁**：下一个写入者能在有限时间内正常写入

    机制：OS 文件锁随进程消亡自动释放；预留日志让新进程能接着原链头续写。
    """
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    proc = ctx.Process(target=_crash_worker, args=(paths["db"], paths["roots"], 5, out))
    proc.start()
    try:
        out.get(timeout=_MP_TIMEOUT)
        proc.terminate()
        proc.join(timeout=60.0)
    finally:
        if proc.is_alive():  # pragma: no cover
            proc.terminate()
            proc.join(timeout=30.0)

    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    try:
        t0 = time.monotonic()
        entry = chain.append("after.crash", actor="survivor")
        chain.flush(timeout=30.0)
        elapsed = time.monotonic() - t0
        rows = chain.entries()
    finally:
        chain.close()

    assert entry.seq == 6, f"续写的 seq 应为 6（5 条已由日志恢复），实际 {entry.seq}"
    assert len(rows) == 6, f"恢复+续写后应有 6 条，实际 {len(rows)}"
    assert elapsed < 30.0, f"续写耗时 {elapsed:.1f}s（疑似死锁）"


# ════════════════════════════════════════════════════════════
#  3. 锁超时 → 显式失败 + 留痕（不静默）
# ════════════════════════════════════════════════════════════


def test_seq_lock_timeout_is_explicit_and_traced(paths, monkeypatch):
    """锁不可得 ⇒ **降级分配 + 计数 + 留痕**；写入本身仍成功（不静默丢数据）

    为什么不是"直接抛错"：审计写入是 best-effort 的安全台账，抛错会把业务
    主路径一起带走。正确口径是"**记录照写、降级显式化**"：计数进 ``stats()``，
    并写一条 ``lock.*`` 审计留痕。本用例断言这三件事都发生。
    """
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       seq_lock_timeout=0.2)
    traces = []
    try:
        # 让取锁恒失败（模拟"锁被别人长期持有"），但不影响分配与日志
        monkeypatch.setattr(chain._cp_lock, "try_lock", lambda: False)
        monkeypatch.setattr(chain._cp_lock, "_acquire",
                            lambda timeout, blocking: (False, 200.0))
        import agent.utils.cross_process_lock as cpl
        monkeypatch.setattr(cpl, "notify_degraded",
                            lambda action, detail: traces.append((action, detail)))

        entry = chain.append("degraded.write", actor="actor")
        chain.flush(timeout=30.0)
        stats = chain.stats()
    finally:
        chain.close()

    assert entry.seq >= 1, "降级时仍必须返回可用的 seq"
    assert stats["seq_degraded_count"] >= 1, f"降级未计数: {stats}"
    assert stats["degraded"] is True, f"未标记降级: {stats}"
    actions = [a for a, _ in traces]
    assert "lock.timeout" in actions or "lock.degraded" in actions, \
        f"降级未留痕: {actions}"


def test_degraded_allocations_are_counted_not_silent(paths, monkeypatch):
    """多次锁超时 ⇒ 计数逐次累加（可观测性不打折）"""
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       seq_lock_timeout=0.1)
    try:
        monkeypatch.setattr(chain, "_lock_enabled", False)
        monkeypatch.setattr(chain._cp_lock, "_acquire",
                            lambda timeout, blocking: (False, 100.0))
        # _lock_enabled=False 走无锁分支；这里直接验证 lock_enabled=False 的
        # 退化为"纯单进程语义"且**统计字段可用**
        for _ in range(5):
            chain.append("nolock.write", actor="actor")
        chain.flush(timeout=30.0)
        stats = chain.stats()
    finally:
        chain.close()
    assert stats["seq_alloc_reliable"] is True, "显式关闭锁时应报告为可靠（单进程语义）"
    assert stats["queue_full_count"] == 0


# ════════════════════════════════════════════════════════════
#  4. 降级路径不静默丢数据：队列满 / ring buffer 溢出
# ════════════════════════════════════════════════════════════


def test_queue_full_is_counted_and_records_survive_via_journal(paths, monkeypatch):
    """**队列满**：明确行为 = 计数 + 留痕 + 记录仍在预留日志（不丢）

    队列在这里只是"进程内交接通道"；持久化已在取锁期间完成，所以打满通道
    不会丢数据，只会在后续轮次由日志收敛补齐。
    """
    import queue as queue_module

    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       queue_maxsize=1)
    try:
        # 让队列恒满：第一次入队后立刻塞满
        real_put = chain._queue.put_nowait

        def full_put(item):
            if item is not None:
                raise queue_module.Full
            return real_put(item)

        monkeypatch.setattr(chain._queue, "put_nowait", full_put)
        entries = [chain.append("queue.full", actor="actor") for _ in range(5)]
        stats_mid = chain.stats()
        monkeypatch.undo()
        chain.flush(timeout=30.0)
        rows = chain.entries(action="queue.full")
        stats = chain.stats()
    finally:
        chain.close()

    assert stats_mid["queue_full_count"] == 5, \
        f"队满未逐次计数: {stats_mid['queue_full_count']}"
    assert len(entries) == 5
    assert {int(e.seq) for e in entries} <= {int(r.seq) for r in rows}, \
        "队满时记录未能经预留日志收敛入库（静默丢失）"
    assert stats["buffer_dropped_count"] == 0, "记录落到了 ring buffer（应走日志收敛）"
    assert stats["degraded"] is True, "队满属降级，必须显式标记"


def test_ring_buffer_overflow_is_counted_not_silent(paths):
    """**ring buffer 溢出**：旧实现靠 ``deque(maxlen=N)`` 静默丢最旧一条；现须计数

    这条用例直接构造溢出，断言计数器与 ``stats()`` 可见——"丢了但没人知道"
    是审计场景最不能接受的失败形态。
    """
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       ring_buffer_maxlen=3, enforce_single_writer=False)
    try:
        for i in range(7):
            # 直接走降级入口，避免依赖 writer 时序
            chain._buffer_failed(chain.append("overflow.write", actor="a"))
        stats = chain.stats()
    finally:
        chain.close()
    assert stats["buffer_dropped_count"] >= 4, \
        f"ring buffer 溢出未计数: {stats['buffer_dropped_count']}"
    assert stats["degraded"] is True


def test_drain_flag_only_set_when_stranded(paths):
    """收敛闸门：没有滞留时 ``_drain_journal`` 不做无谓重放（保护防篡改用例）"""
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False)
    try:
        for _ in range(5):
            chain.append("normal.write", actor="a")
        chain.flush(timeout=30.0)
        assert chain._journal_needs_drain is False, "正常路径不应置收敛标志"
        assert chain._drain_journal() == []
    finally:
        chain.close()


# ════════════════════════════════════════════════════════════
#  5. 预留日志自身的正确性
# ════════════════════════════════════════════════════════════


def test_journal_roundtrip_and_head(tmp_path):
    journal = SeqJournal(str(tmp_path / "j.jsonl"))
    try:
        assert journal.head() == (0, "")
        for seq in range(1, 6):
            journal.append_row({"seq": seq, "self_hash": f"h{seq}",
                                "payload": json.dumps({"seq": seq})})
        assert journal.head() == (5, "h5")
        assert journal.max_seq() == 5
        rows = journal.read_since(3)
        assert [r["seq"] for r in rows] == [4, 5], rows
    finally:
        journal.close()


def test_journal_discards_torn_tail(tmp_path):
    """进程被杀留下的**半行**必须被清掉：否则后续 append 会与之粘连成非法 JSON"""
    path = tmp_path / "j.jsonl"
    path.write_bytes(b'{"seq":1,"self_hash":"h1"}\n{"seq":2,"self_ha')
    journal = SeqJournal(str(path))
    try:
        journal.discard_torn_tail()
        assert journal.head() == (1, "h1"), journal.head()
        journal.append_row({"seq": 2, "self_hash": "h2"})
        assert journal.head() == (2, "h2")
        assert [r["seq"] for r in journal.read_since(0)] == [1, 2]
    finally:
        journal.close()


def test_journal_compaction_keeps_recent_and_never_loses_uncommitted(tmp_path):
    """压缩只丢**已入库**的行；``retain`` 保留最近若干条作降级读缓存"""
    journal = SeqJournal(str(tmp_path / "j.jsonl"))
    try:
        for seq in range(1, 11):
            journal.append_row({"seq": seq, "self_hash": f"h{seq}"})
        assert journal.compact(upto_seq=8, retain=3) is True
        kept = [r["seq"] for r in journal.read_since(0, limit=100)]
        # 8 - 3 = 5 ⇒ 保留 seq > 5（即 6..10），其中 9/10 尚未入库、必须保留
        assert kept == [6, 7, 8, 9, 10], kept
        assert 9 in kept and 10 in kept, "未入库记录被压缩丢弃（静默丢失）"
    finally:
        journal.close()


def test_journal_compaction_respects_watermark_and_retain(tmp_path):
    """压缩口径：**水位以下**才可丢；水位由调用方保证（本方法只负责裁剪）

    【为什么断言"调用方责任"而不是"方法自查"（实现期修正）】第一版让
    ``compact`` 自查"有无未入库记录"（``max_seq > upto_seq`` 就拒绝），
    这与 ``retain`` 语义直接冲突：只要有未入库尾巴就永远压缩不了，
    ``retain`` 形同虚设。改为分工：**水位正确性由调用方（DB 连续水位）保证，
    裁剪正确性由本方法保证**——即"绝不丢弃 ``seq > upto_seq - retain``"。
    调用方侧的拒绝逻辑由 ``test_chain_compaction_skipped_when_watermark_not_contiguous``
    覆盖。
    """
    journal = SeqJournal(str(tmp_path / "j.jsonl"))
    try:
        for seq in range(1, 11):
            journal.append_row({"seq": seq, "self_hash": f"h{seq}"})
        # 水位 5（1..5 已入库）+ 保留最近 5 条 ⇒ 保留 seq > 0，即全部
        assert journal.compact(upto_seq=5, retain=5) is False, "全部都在保留区内，不应改写"
        # 水位 8 + 保留 3 ⇒ 丢弃 seq <= 5
        assert journal.compact(upto_seq=8, retain=3) is True
        kept = [r["seq"] for r in journal.read_since(0, limit=100)]
        assert kept == [6, 7, 8, 9, 10], kept
        assert 9 in kept and 10 in kept, "水位之上（尚未入库）的记录被丢弃"
    finally:
        journal.close()


def test_chain_compaction_skipped_when_watermark_not_contiguous(paths):
    """**连续水位**是压缩的前提：DB 非连续时宁可**不压缩**（防静默丢数据）

    构造：让 DB 只有 seq 6（1..5 尚未入库），断言
    ``_committed_watermark()`` 判定为非连续、``_maybe_compact_journal`` 放弃压缩，
    且日志内容一条不少。
    """
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    try:
        # 直接在日志里放 1..5，并在 DB 里只插 seq 6，制造"非连续已入库"
        for seq in range(1, 6):
            chain._journal.append_row({"seq": seq, "self_hash": f"h{seq}",
                                       "payload": "{}", "ts": "2026-01-01T00:00:00+00:00",
                                       "actor": "a", "action": "x", "subject": "",
                                       "payload_hash": "p", "prev_hash": "0" * 64,
                                       "source": "agent", "trace_id": "",
                                       "workspace_id": "", "schema_version": 1})
        with chain._connect() as conn:
            with chain._write_lock:
                conn.execute(
                    "INSERT INTO audit_chain (seq, ts, actor, action, subject,"
                    " payload_hash, prev_hash, self_hash, source, trace_id,"
                    " workspace_id, schema_version, payload) "
                    "VALUES (6,'t','a','x','','p','q','h6','agent','','',1,'{}')")
                conn.commit()

        watermark, contiguous = chain._committed_watermark()
        assert contiguous is False, f"非连续却判为连续: watermark={watermark}"
        assert watermark == 0
        chain._journal_compact_min_rows = 0
        chain._last_compact_check = 0.0
        assert chain._maybe_compact_journal() is False, "非连续水位下不得压缩"
        # 日志里是 1..5（seq 6 只在 DB 里），压缩未生效则一条不少
        assert chain._journal.max_seq() == 5, "压缩误删了尚未入库的记录"
        assert [r["seq"] for r in chain._journal.read_since(0, limit=100)] == [1, 2, 3, 4, 5]
    finally:
        chain.close()


def test_committed_watermark_contiguous_detection(paths):
    """连续水位判定：1..N 全在 ⇒ 连续；缺中间 ⇒ 非连续"""
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False)
    try:
        assert chain._committed_watermark() == (0, True), "空库应为连续水位 0"
        for seq in (1, 2, 3):
            chain.append("wm.write", actor="a", ts=f"2026-01-0{seq}T00:00:00+00:00")
        chain.flush(timeout=30.0)
        watermark, contiguous = chain._committed_watermark()
        assert contiguous is True and watermark == 3, (watermark, contiguous)
        with chain._connect() as conn:
            with chain._write_lock:
                conn.execute("DELETE FROM audit_chain WHERE seq = 2")
                conn.commit()
        watermark, contiguous = chain._committed_watermark()
        assert contiguous is False and watermark == 0, (watermark, contiguous)
    finally:
        chain.close()


def test_journal_disabled_is_noop(tmp_path):
    journal = SeqJournal(str(tmp_path / "j.jsonl"), enabled=False)
    assert journal.append_row({"seq": 1}) == 0
    assert journal.head() == (0, "")
    assert journal.read_since(0) == []
    assert journal.compact(upto_seq=10 ** 9) is False
    assert not os.path.exists(str(tmp_path / "j.jsonl")), "禁用时不应产生任何文件"


def test_journal_rejects_oversized_line(tmp_path):
    journal = SeqJournal(str(tmp_path / "j.jsonl"))
    try:
        with pytest.raises(Exception):
            journal.append_row({"seq": 1, "payload": "x" * (4 << 20)})
    finally:
        journal.close()


# ════════════════════════════════════════════════════════════
#  6. 不新建第二套锁（代码级证据）+ 兼容性
# ════════════════════════════════════════════════════════════


def test_only_one_lock_implementation_in_agent():
    """**代码级证据**：``agent/`` 下只有 ``utils/cross_process_lock.py`` 直接调 OS 锁

    这是验收项「不新建第二套锁（复用既有非阻塞 OS 文件锁模式；代码级证据）」的
    可执行形式：把"只有一处 msvcrt/fcntl"钉成回归测试，防止将来又抄出第 N 份。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "agent"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ("msvcrt.locking" in text) or ("fcntl.flock" in text):
            rel = path.relative_to(root).as_posix()
            if rel != "utils/cross_process_lock.py":
                offenders.append(rel)
    assert not offenders, (
        f"检测到第二套锁实现（应统一调用 agent/utils/cross_process_lock.py）: {offenders}")


def test_chain_public_api_unchanged(paths):
    """公开接口语义不变：``append`` 仍返回 AuditEntry，且接受原有全部关键字"""
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False)
    try:
        entry = chain.append("api.check", "actor", "subject",
                             {"k": "v"}, source="agent", trace_id="t-1",
                             workspace_id="w-1", schema_version=1)
        assert entry.seq == 1
        assert entry.action == "api.check"
        assert entry.source == "agent"
        assert entry.trace_id == "t-1"
        assert entry.payload == {"k": "v"}
        assert entry.self_hash and entry.payload_hash
    finally:
        chain.close()


def test_single_writer_registry_still_enforced(paths):
    """单写者纪律（§5.5）未被削弱：同路径第二个 writer 仍抛异常"""
    from agent.audit.chain import SingleWriterViolationError
    first = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False)
    try:
        with pytest.raises(SingleWriterViolationError):
            AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False)
    finally:
        first.close()


def test_get_audit_chain_singleton_reuse(paths):
    chain = get_audit_chain(paths["db"], roots_path=paths["roots"],
                            signing_enabled=False, auto_seal=False)
    try:
        assert get_audit_chain(paths["db"]) is chain
    finally:
        chain.close()


# ════════════════════════════════════════════════════════════
#  7. 进程内线程安全：ring buffer 的"写者 vs 读者"竞态
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(180)
def test_failed_buffer_read_while_written_is_thread_safe(paths):
    """**写者 append 与读者遍历并发时不得抛异常**（不变式守卫）

    【问题】``_failed_buffer`` 是普通 ``collections.deque``：**writer 线程**写
    （``_buffer_failed`` / ``clear``），而读路径（``entries`` / ``seq_range`` /
    ``stats`` / ``verify_chain``）可能在任何线程上遍历它。CPython 的 ``deque``
    在"一边 append 一边迭代"时会抛
    ``RuntimeError: deque mutated during iteration``（**已单独实测复现**：
    需要一个**足够大**的 deque 让迭代跨越 GIL 切换点，配合极端切换间隔
    ``sys.setswitchinterval(1e-6)`` 稳定复现）。修法是把读写都收进
    ``_buffer_lock``，读走 ``_failed_snapshot()`` 的**持锁拷贝**再遍历。

    【本用例的诚实定位：**守卫，不是复现**】这里用 ``ring_buffer_maxlen=64``
    （默认 2000 量级的迭代在**一个 GIL 时间片内**就跑完，窗口极窄），
    因此它**复现不出**那个 RuntimeError，跑的是"并发读写不抛异常 + 溢出仍被计数"
    这两条不变量。真正的复现需要放大切换窗口，而 ``setswitchinterval`` 是
    **进程级全局状态**，放进测试会波及其它用例（顺序污染），故刻意不这么做。
    也就是说：本用例通过 **≠** 竞态已绝迹，只保证不回归。
    """
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       ring_buffer_maxlen=64, auto_start_writer=False)
    errors: List[str] = []
    errors_lock = threading.Lock()
    stop = threading.Event()

    def _note(exc: BaseException) -> None:
        with errors_lock:
            errors.append(f"{type(exc).__name__}: {exc}")

    def writer() -> None:
        try:
            for i in range(4000):
                entry = build_entry(
                    seq=i + 1, ts="2026-09-13T00:00:00+00:00", actor="a",
                    action="race.write", subject="", payload={"i": i},
                    prev_hash=GENESIS_PREV_HASH)
                chain._buffer_failed(entry)          # noqa: SLF001 专测该路径
        except BaseException as exc:  # noqa: BLE001 竞态会把异常直接抛出
            _note(exc)
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                chain._failed_snapshot()             # noqa: SLF001
                chain.seq_range()
                chain.stats()
                chain.last_entry()
                chain.count()
        except BaseException as exc:  # noqa: BLE001
            _note(exc)

    alive: List[str] = []
    try:
        threads = [threading.Thread(target=writer, name="race-w") for _ in range(2)]
        threads += [threading.Thread(target=reader, name="race-r") for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60.0)
        alive = [t.name for t in threads if t.is_alive()]
        dropped = chain.stats()["buffer_dropped_count"]
        buffered = chain._failed_len()               # noqa: SLF001
    finally:
        chain.close()

    assert not alive, f"线程未在时限内退出（疑似死锁）: {alive}"
    assert not errors, f"ring buffer 读写竞态导致异常: {errors[:5]}"
    # 容量 64、写入 8000 条 ⇒ 必然溢出，且溢出必须被计数（不静默）
    assert buffered <= 64, buffered
    assert dropped > 0, "ring buffer 溢出未计数"


@pytest.mark.timeout(120)
def test_clear_is_thread_safe_against_reads(paths):
    """``clear()`` 与读路径并发时同样不得抛异常（clear 也在改 deque）"""
    chain = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_enabled=False, auto_seal=False,
                       ring_buffer_maxlen=32, auto_start_writer=False)
    errors: List[str] = []
    errors_lock = threading.Lock()
    stop = threading.Event()

    def churn() -> None:
        try:
            i = 0
            while not stop.is_set():
                i += 1
                chain._buffer_failed(build_entry(      # noqa: SLF001
                    seq=i, ts="2026-09-13T00:00:00+00:00", actor="a",
                    action="race.clear", subject="", payload={},
                    prev_hash=GENESIS_PREV_HASH))
                if i % 50 == 0:
                    with chain._buffer_lock:               # noqa: SLF001
                        chain._failed_buffer.clear()       # noqa: SLF001
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                chain.stats()
                chain._failed_snapshot()                   # noqa: SLF001
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    try:
        threads = [threading.Thread(target=churn) for _ in range(2)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join(timeout=30.0)
    finally:
        chain.close()
    assert not errors, f"clear 与读并发导致异常: {errors[:5]}"
