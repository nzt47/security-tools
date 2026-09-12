"""TASK-S8-02 JSONL 追加 / 分片写入的多进程安全加固 单元测试

覆盖（对齐任务书 §四 验收清单）：

1. **线程**并发追加：只产生格式良好的整行、一条不丢；
2. **多进程**并发追加（spawn，4 进程 × 100 条）：行数守恒 + 全部可解析 +
   无交错/撕行 —— 这是本次加固的**核心证明**；
3. 取锁失败的**降级路径**：被计数、被留痕、**不静默丢**（本轮实现为
   "有限等待 → 内存排队 → 下次成功取锁时排空"，缓冲打满才是显式失败）；
4. `archive_daily_file` 的**原子性**：替换失败时原文件逐字完好、记录一条不少；
5. 归档**不删**：总记录数守恒 + 分片命名 `<stem>-YYYYMMDD<ext>` 不变；
6. 坏行**可见**：`skipped_line_count` 计数 + 限流告警（腐坏不再静默被跳过）。

【用例落盘纪律（S3-02/S3-03 两次污染真实 data/ 的教训）】
    所有用例显式传 `tmp_path`；autouse fixture 把 `CP_EVENTS_DIR` 指向 tmp 并
    把锁留痕钩子换成静音探针，确保**任何路径**都不会写仓库真实 `data/`。
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import threading
from datetime import date
from pathlib import Path
from typing import Dict, List, Set

import pytest

from agent.observability import events as ev
from agent.skills_mgmt import log_archiver as la
from agent.skills_mgmt import review_gate as rg
from agent.utils.cross_process_lock import (
    CrossProcessLock,
    reset_conflict_cooldown,
    reset_lock_metrics,
    set_notify_hook,
)

pytestmark = [pytest.mark.unit]

#: Windows 上进程创建（spawn + 解释器启动）比 POSIX 慢得多，上界留足
_MP_TIMEOUT = 90.0
_ENVELOPE_KEYS = set(ev.ENVELOPE_FIELDS)
_TODAY = date.today().isoformat()


# ════════════════════════════════════════════════════════════
#  隔离（绝不触碰真实 data/）
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每例：事件目录指向 tmp + 清零计数 + 静音锁留痕

    【为什么静音留痕】锁降级默认会写审计链与事件流（真实 `data/`）。
    用例里换成探针，既避免污染真实目录，也让"必须留痕"能被显式断言
    （见 `test_lock_unavailable_is_counted_traced_and_not_dropped`）。
    """
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    monkeypatch.delenv(ev.ENV_ENABLED, raising=False)
    monkeypatch.delenv(ev.ENV_ARCHIVE, raising=False)
    monkeypatch.delenv(ev.ENV_LOCK_ENABLED, raising=False)
    monkeypatch.delenv(ev.ENV_LOCK_TIMEOUT_SEC, raising=False)
    monkeypatch.delenv(ev.ENV_PENDING_MAX, raising=False)
    monkeypatch.delenv(la.ENV_ARCHIVE_LOCK_ENABLED, raising=False)
    monkeypatch.delenv(la.ENV_ARCHIVE_LOCK_TIMEOUT_SEC, raising=False)
    ev.reset_event_stores()
    ev.reset_read_stats()
    la.reset_archive_stats()
    reset_lock_metrics()
    reset_conflict_cooldown()
    set_notify_hook(lambda action, detail: None)
    yield
    set_notify_hook(None)
    ev.reset_event_stores()
    ev.reset_read_stats()
    la.reset_archive_stats()


def _active(tmp_path) -> str:
    return str(tmp_path / "events" / ev.ACTIVE_FILENAME)


def _lines(path) -> List[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def _records(directory) -> List[str]:
    """目录下所有 JSONL（活动文件 + 分片）的记录行（忽略 tmp 残留）"""
    out: List[str] = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        if path.name.endswith(".tmp"):
            continue
        out.extend(line for line in _lines(path) if line.strip())
    return out


# ════════════════════════════════════════════════════════════
#  子进程 worker（spawn 上下文要求模块级可 pickle 函数）
# ════════════════════════════════════════════════════════════


def _mp_append_worker(path: str, worker: int, count: int, archive: bool,
                      result) -> None:  # pragma: no cover - 子进程内执行
    """子进程：向**同一个** events.jsonl 追加 count 条，回传本进程计数"""
    from agent.observability.events import EventStore
    from agent.utils.cross_process_lock import set_notify_hook

    # 用例纪律：子进程也静音锁留痕，避免任何路径写到仓库真实 data/
    set_notify_hook(lambda action, detail: None)
    store = EventStore(path, archive=archive)
    for i in range(count):
        store.emit("cost", {"worker": worker, "i": i},
                   correlation_id=f"w{worker}", idempotency_key=f"w{worker}:{i}")
    store.close()                      # 关闭前会尽力排空降级缓冲
    stats = store.stats()
    result.put({
        "worker": worker,
        "write_count": stats["write_count"],
        "durable_write_count": stats["durable_write_count"],
        "lock_write_count": stats["lock_write_count"],
        "lock_bypass_count": stats["lock_bypass_count"],
        "pending_unsaved": stats["pending_unsaved"],
        "failure_count": stats["failure_count"],
    })


# ════════════════════════════════════════════════════════════
#  1. 线程并发追加
# ════════════════════════════════════════════════════════════


def test_threaded_appends_are_well_formed_and_lossless(tmp_path):
    """多线程并发 append：行数守恒、每行都是完整信封、无重复 id"""
    store = ev.EventStore(_active(tmp_path), archive=False)
    threads_n, per_thread = 8, 40
    errors: List[BaseException] = []

    def worker(tag: int) -> None:
        try:
            for i in range(per_thread):
                store.emit("cost", {"t": tag, "i": i},
                           correlation_id=f"t{tag}", idempotency_key=f"t{tag}:{i}")
        except BaseException as e:  # noqa: BLE001 线程内异常必须显式收集
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60.0)

    assert not errors, f"线程内异常: {errors[:3]}"
    total = threads_n * per_thread
    rows = _lines(store.path)
    assert len(rows) == total, f"行数不守恒: {len(rows)} != {total}"
    parsed = [json.loads(line) for line in rows]      # 任一行撕坏 ⇒ 这里必炸
    assert all(set(rec) == _ENVELOPE_KEYS for rec in parsed), "存在非信封行"
    assert len({rec["event_id"] for rec in parsed}) == total
    stats = store.stats()
    assert stats["write_count"] == total
    assert stats["pending_unsaved"] == 0
    assert stats["lock_bypass_count"] == 0, stats
    assert stats["durable_write_count"] == total


def test_append_jsonl_locked_is_thread_safe(tmp_path):
    """分片写入原语在多线程下不撕行/不丢行（技能审计分片走的就是它）"""
    path = tmp_path / "skills_assessment_events.jsonl"
    threads_n, per_thread = 8, 30

    def worker(tag: int) -> None:
        for i in range(per_thread):
            la.append_jsonl_locked(path, json.dumps({"t": tag, "i": i}))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60.0)

    total = threads_n * per_thread
    rows = _lines(path)
    assert len(rows) == total, f"行数不守恒: {len(rows)} != {total}"
    recs = [json.loads(line) for line in rows]
    assert len({(r["t"], r["i"]) for r in recs}) == total
    stats = la.archive_stats()
    assert stats["append_failures"] == 0
    assert stats["append_unlocked"] == 0, "同进程线程不应走无锁降级"
    assert stats["append_locked"] == total


# ════════════════════════════════════════════════════════════
#  2. 多进程并发追加（★ 核心证明）
# ════════════════════════════════════════════════════════════


@pytest.mark.timeout(120)
def test_multi_process_appends_conserve_every_line(tmp_path):
    """4 个**真进程**并发追加同一 events.jsonl：行数守恒、无交错/撕行

    断言口径（三项互相独立，缺一不可）：

    1. **行数守恒**：文件物理行数 == 4 × 100，且每行都能 `json.loads`；
    2. **无撕裂/交错**：每行都是完整 events.v1 信封（7 键）+ 载荷可解析；
       —— 若两个进程的字节交错，必然出现"半行 + 半行"⇒ 解析失败；
    3. **无丢失/无重复**：id 集合 == 期望集合（event_id 由幂等键确定性派生）；
    4. 附加：各子进程 `lock_bypass_count == 0`（说明互斥真的生效，
       而不是"大家都无锁写、恰好没撞上"）。
    """
    procs, per_proc = 4, 100
    base = tmp_path / "events"
    base.mkdir(parents=True, exist_ok=True)
    path = str(base / ev.ACTIVE_FILENAME)

    ctx = multiprocessing.get_context("spawn")
    result = ctx.Queue()
    workers = [ctx.Process(target=_mp_append_worker,
                           args=(path, w, per_proc, False, result))
               for w in range(procs)]
    for proc in workers:
        proc.start()
    try:
        for proc in workers:
            proc.join(timeout=_MP_TIMEOUT)
        assert all(not p.is_alive() for p in workers), "子进程超时未退出（疑似死锁）"
        assert all(p.exitcode == 0 for p in workers), \
            f"子进程非零退出: {[p.exitcode for p in workers]}"
        reports: List[Dict[str, int]] = [result.get(timeout=_MP_TIMEOUT)
                                         for _ in workers]
    finally:
        for proc in workers:
            if proc.is_alive():  # pragma: no cover - 兜底，避免孤儿进程
                proc.terminate()
                proc.join(timeout=10.0)

    total = procs * per_proc
    rows = _lines(path)
    assert len(rows) == total, f"多进程写入行数不守恒: {len(rows)} != {total}"

    parsed = []
    for idx, line in enumerate(rows, start=1):
        rec = json.loads(line)                        # 撕行/交错 ⇒ 必炸
        assert set(rec) == _ENVELOPE_KEYS, f"第 {idx} 行非完整信封: {line[:120]!r}"
        parsed.append(rec)
    assert all(isinstance(r["payload"], dict) for r in parsed)

    expected: Set[str] = set()
    for w in range(procs):
        for i in range(per_proc):
            expected.add(ev.build_event_id("cost", "auto", f"w{w}",
                                           {"worker": w, "i": i}, f"w{w}:{i}"))
    got_ids = [rec["event_id"] for rec in parsed]
    # 证据行（`pytest -s` 可见；断言失败时也已打印，便于定位是"丢"还是"撕"）
    print(f"\n[MP-证据] 进程数={procs} 每进程条数={per_proc} 期望={total} "
          f"物理行数={len(rows)} 可解析信封行数={len(parsed)} "
          f"唯一event_id={len(set(got_ids))} 重复={total - len(set(got_ids))} "
          f"缺失={len(expected - set(got_ids))} 多余={len(set(got_ids) - expected)} "
          f"撕行/交错=0（任一行不可解析则本用例在上一行 json.loads 处即失败）\n"
          f"[MP-证据] 各子进程计数={reports}")
    assert len(set(got_ids)) == total, "存在重复 event_id（覆盖写/重复行）"
    assert set(got_ids) == expected, "有事件丢失或换成了非期望事件"

    assert sum(r["write_count"] for r in reports) == total, reports
    assert sum(r["lock_write_count"] for r in reports) == total, \
        f"并非所有写入都在持锁下完成: {reports}"
    assert sum(r["lock_bypass_count"] for r in reports) == 0, \
        f"出现取锁降级（CI 负载下的偶发可接受，但本次为 0 才是强证据）: {reports}"
    assert sum(r["pending_unsaved"] for r in reports) == 0, reports
    assert sum(r["failure_count"] for r in reports) == 0, reports


# ════════════════════════════════════════════════════════════
#  3. 降级路径：计数 + 留痕 + 不静默丢
# ════════════════════════════════════════════════════════════


def test_lock_unavailable_is_counted_traced_and_not_dropped(tmp_path):
    """取锁失败 ⇒ 有限等待 → 内存排队 → 下次成功取锁时排空（一行不丢）

    【为什么不是"无锁照样写"】归档方正在 read-modify-write 时，无锁追加会落在
    **即将被 `os.replace` 淘汰的旧 inode** 上 ⇒ 静默丢失。排队到"下次拿到锁"
    再写，则必然写进**当前**活动文件。
    """
    base = tmp_path / "events"
    base.mkdir(parents=True, exist_ok=True)
    path = str(base / ev.ACTIVE_FILENAME)
    store = ev.EventStore(path, archive=False)

    seen: List[str] = []
    set_notify_hook(lambda action, detail: seen.append(action))

    holder = CrossProcessLock(la.jsonl_lock_path(path), name="holder")
    assert holder.try_lock() is True
    try:
        assert store.emit("cost", {"n": 1}) is not None, "降级应仍被受理（不得丢）"
        stats = store.stats()
        assert stats["lock_bypass_count"] == 1, stats
        assert stats["pending_unsaved"] == 1, stats
        assert stats["durable_write_count"] == 0, stats
        assert not (base / ev.ACTIVE_FILENAME).exists(), "降级期间不应写文件"
        # 留痕：锁原语把超时/冲突上报给 notify 钩子（生产环境写审计链 + 事件流）
        assert "lock.timeout" in seen or "lock.conflict" in seen, seen
    finally:
        holder.release()

    assert store.emit("cost", {"n": 2}) is not None
    stats = store.stats()
    assert stats["pending_unsaved"] == 0, "下一次成功取锁应排空积压"
    assert stats["durable_write_count"] == 2, stats
    rows = ev.read_events(directory=str(base))
    assert sorted(r.payload["n"] for r in rows) == [1, 2], "降级行必须一条不少"


def test_pending_buffer_overflow_fails_explicitly(tmp_path, monkeypatch):
    """缓冲打满 = **显式失败**（计数 + 上抛，由 append 计入 failure_count）

    这是降级链的终端态：宁可"显式失败让调用方知道"，也绝不静默丢。
    """
    monkeypatch.setenv(ev.ENV_PENDING_MAX, "2")
    base = tmp_path / "events"
    base.mkdir(parents=True, exist_ok=True)
    path = str(base / ev.ACTIVE_FILENAME)
    store = ev.EventStore(path, archive=False)

    holder = CrossProcessLock(la.jsonl_lock_path(path), name="holder")
    assert holder.try_lock() is True
    try:
        assert store.emit("cost", {"n": 1}) is not None
        assert store.emit("cost", {"n": 2}) is not None
        assert store.emit("cost", {"n": 3}) is None, "缓冲满必须显式失败（返回 None）"
        stats = store.stats()
        assert stats["pending_unsaved"] == 2, stats
        assert stats["dropped_count"] == 1, stats
        assert stats["failure_count"] == 1, stats
        assert "缓冲已满" in stats["last_error"], stats["last_error"]
    finally:
        holder.release()

    assert store.flush() is True
    rows = ev.read_events(directory=str(base))
    assert sorted(r.payload["n"] for r in rows) == [1, 2], "已入队的行不得丢"
    assert store.stats()["pending_unsaved"] == 0


def test_lock_can_be_disabled_by_env(tmp_path, monkeypatch):
    """`CP_EVENTS_LOCK_ENABLED=0`（测试/应急开关）：不建锁文件，写入仍成功"""
    monkeypatch.setenv(ev.ENV_LOCK_ENABLED, "0")
    path = _active(tmp_path)
    store = ev.EventStore(path, archive=False)
    assert store.stats()["lock_enabled"] is False
    assert store.emit("cost", {"n": 1}) is not None
    assert Path(path).exists()
    assert not Path(path + ".lock").exists(), "关锁时不应创建锁文件"


def test_flush_reports_pending_when_lock_still_unavailable(tmp_path):
    """`flush()` 在锁仍不可用时返回 False（**不谎报已落盘**）"""
    base = tmp_path / "events"
    base.mkdir(parents=True, exist_ok=True)
    path = str(base / ev.ACTIVE_FILENAME)
    store = ev.EventStore(path, archive=False)
    holder = CrossProcessLock(la.jsonl_lock_path(path), name="holder")
    assert holder.try_lock() is True
    try:
        store.emit("cost", {"n": 1})
        assert store.flush() is False
        assert store.stats()["pending_unsaved"] == 1
    finally:
        holder.release()
    assert store.flush() is True
    assert store.stats()["pending_unsaved"] == 0


# ════════════════════════════════════════════════════════════
#  4. 归档：原子性 / 不删 / 加锁
# ════════════════════════════════════════════════════════════


def test_archive_rewrite_failure_loses_no_record(tmp_path, monkeypatch):
    """替换中途失败（模拟磁盘/共享冲突）：原文件逐字完好、记录一条不少

    【为什么"先写分片、后替换"能扛住】崩/失败在替换之前 ⇒ 历史行既在分片里、
    也在**未被改动的活动文件**里（重复而非丢失）。旧实现是"先截断活动文件、
    再写分片"，同期失败 = 那批记录**永久消失**。
    """
    p = tmp_path / "skills_assessment_events.jsonl"
    old = [json.dumps({"ts": "2020-01-01T00:00:00", "skill_id": f"s{i}"})
           for i in range(3)]
    today_line = json.dumps({"ts": f"{_TODAY}T10:00:00", "skill_id": "today"})
    original = "\n".join(old + [today_line]) + "\n"
    p.write_text(original, encoding="utf-8")

    def boom(*args, **kwargs):
        raise PermissionError("模拟 os.replace 失败（Windows 共享冲突）")

    real_replace = os.replace
    monkeypatch.setattr(os, "replace", boom)

    out = la.archive_daily_file(p)
    assert out["archived"] == 0
    assert out["today"] == 1
    assert p.read_text(encoding="utf-8") == original, "活动文件被破坏（非原子写）"
    found = set(_records(tmp_path))
    assert set(old) <= found, "历史记录丢失（归档不删语义被破坏）"
    assert not list(tmp_path.glob("*.tmp")), "临时文件未清理"
    assert la.archive_stats()["archive_write_failures"] == 1

    # 失败不写记忆 ⇒ 解除故障后可补做；届时只会产生**重复**，不会丢
    monkeypatch.setattr(os, "replace", real_replace)
    out2 = la.archive_daily_file(p)
    assert out2["archived"] == 3
    assert set(old) <= set(_records(tmp_path))


def test_archive_conserves_records_and_keeps_shard_naming(tmp_path):
    """归档不删：总记录数守恒；分片名固定 `<stem>-YYYYMMDD<ext>`"""
    p = tmp_path / "skills_assessment_events.jsonl"
    recs = [
        json.dumps({"ts": "2020-01-01T00:00:00", "skill_id": "a"}),
        json.dumps({"ts": "2020-01-02T00:00:00", "skill_id": "b"}),
        json.dumps({"ts": "2020-01-02T01:00:00", "skill_id": "c"}),
        json.dumps({"ts": f"{_TODAY}T09:00:00", "skill_id": "d"}),
        json.dumps({"ts": f"{_TODAY}T10:00:00", "skill_id": "e"}),
    ]
    p.write_text("\n".join(recs) + "\n", encoding="utf-8")

    out = la.archive_daily_file(p)
    assert out["archived"] == 3
    assert out["today"] == 2
    assert sorted(Path(f).name for f in out["files"]) == [
        "skills_assessment_events-2020-01-01.jsonl",
        "skills_assessment_events-2020-01-02.jsonl",
    ]
    remaining = _lines(p)
    assert len(remaining) == 2
    assert {json.loads(l)["skill_id"] for l in remaining} == {"d", "e"}
    assert len(_records(tmp_path)) == len(recs), "归档不得删除任何记录"
    assert set(_records(tmp_path)) == set(recs)


def test_archive_skips_round_when_lock_unavailable(tmp_path, monkeypatch):
    """拿不到归档锁 ⇒ 本轮跳过（零结果 + 计数 + 留痕），绝不无锁改写"""
    monkeypatch.setenv(la.ENV_ARCHIVE_LOCK_TIMEOUT_SEC, "0.05")
    p = tmp_path / "skills_assessment_events.jsonl"
    old = [json.dumps({"ts": "2020-01-01T00:00:00", "skill_id": f"s{i}"})
           for i in range(3)]
    today_line = json.dumps({"ts": f"{_TODAY}T10:00:00", "skill_id": "today"})
    original = "\n".join(old + [today_line]) + "\n"
    p.write_text(original, encoding="utf-8")

    seen: List[str] = []
    set_notify_hook(lambda action, detail: seen.append(action))

    holder = CrossProcessLock(la.jsonl_lock_path(p), name="archiver")
    assert holder.try_lock() is True
    try:
        out = la.archive_daily_file(p)
        assert out == {"archived": 0, "files": [], "today": 0}
        assert la.archive_stats()["archive_lock_skips"] == 1
        assert [a for a in seen if a.startswith("lock.")], seen
        assert p.read_text(encoding="utf-8") == original, "跳过时必须一行未动"
        assert len(list(tmp_path.glob("*.jsonl"))) == 1, "跳过时不得产出分片"
    finally:
        holder.release()

    # 记忆未写 ⇒ 下一次调用能补做（不能"跳过一次就当天再也不归档"）
    out2 = la.archive_daily_file(p)
    assert out2["archived"] == 3, out2
    assert len(_records(tmp_path)) == 4


def test_archive_lock_path_is_separate_from_target(tmp_path):
    """锁文件必须与被归档文件**分离**（否则 replace 会把锁丢到旧 inode 上）"""
    p = tmp_path / "events.jsonl"
    lock_path = la.jsonl_lock_path(p)
    assert lock_path != str(p)
    assert lock_path == str(Path(p).resolve()) + ".lock"


def test_lock_path_rule_matches_archiver(tmp_path):
    """★ events 侧与 log_archiver 侧必须派生到**同一个**锁文件

    两侧各写一遍派生逻辑，一旦漂移，追加方与归档方就不再互斥（表现为
    "加了锁还是撕行/丢行"且毫无报错）。本用例把这条不变式钉死。
    """
    path = _active(tmp_path)
    store = ev.EventStore(path, archive=False)
    stats = store.stats()
    assert stats["lock_path"] == la.jsonl_lock_path(path), stats
    assert stats["lock_path"].endswith(".lock")
    assert stats["lock_path"] != path


# ════════════════════════════════════════════════════════════
#  5. 读侧腐坏可见化
# ════════════════════════════════════════════════════════════


def test_corrupt_lines_are_counted_and_warned(tmp_path, caplog):
    """坏行不再静默跳过：计数进 `stats()` + 限流告警"""
    path = _active(tmp_path)
    store = ev.EventStore(path, archive=False)
    store.emit("cost", {"n": 1})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json}\n")                        # 非法 JSON
        fh.write("[1, 2, 3]\n")                         # 合法 JSON 但非 dict
        fh.write("x" * (ev.MAX_LINE_BYTES + 16) + "\n")  # 超长行
    ev.reset_read_stats()

    with caplog.at_level(logging.WARNING, logger="agent.observability.events"):
        rows = store.read()

    assert len(rows) == 1, "好行仍应被读出"
    stats = store.stats()
    assert stats["skipped_line_count"] == 2, stats
    assert stats["skipped_oversized_line_count"] == 1, stats
    assert any("不可解析行" in rec.getMessage() for rec in caplog.records), \
        [rec.getMessage() for rec in caplog.records]


def test_corrupt_counter_is_visible_on_cold_start(tmp_path):
    """冷启动加载幂等索引时的坏行同样计数（崩溃/撕行最可能在此暴露）"""
    path = _active(tmp_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('{"event_id": "ev_x", "type": "cost"}\n{bad json}\n',
                          encoding="utf-8")
    ev.reset_read_stats()
    store = ev.EventStore(path, archive=False)
    assert store.stats()["skipped_line_count"] == 1
    assert "ev_x" in store.known_event_ids


# ════════════════════════════════════════════════════════════
#  6. 技能审计分片写入方（黄线：走同一把跨进程锁）
# ════════════════════════════════════════════════════════════


class _FakeAudit:
    """替身：绝不让用例把留痕写进真实审计链"""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def record(self, action, **kwargs):
        self.calls.append(action)
        return object()


def test_skills_shard_writers_go_through_cross_process_lock(tmp_path, monkeypatch):
    """`review_gate.audit_exemption` 与 `service._emit_assessment_event`
    都持跨进程锁追加，且载荷形状不变"""
    import agent.audit as audit_pkg
    fake = _FakeAudit()
    monkeypatch.setattr(audit_pkg, "audit", fake, raising=False)

    # ── 复核豁免审计（review_gate）──
    audit_path = tmp_path / "skills_mgmt_review_audit.jsonl"
    monkeypatch.setattr(rg, "_audit_file", lambda: str(audit_path))
    rg.audit_exemption("skill-1", actor="tester", reason="unit-test")
    lines = _lines(audit_path)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert set(rec) == {"ts", "event", "skill_id", "actor", "reason"}, rec
    assert rec["event"] == "review_waiver_publish"
    assert rec["skill_id"] == "skill-1"
    assert Path(la.jsonl_lock_path(audit_path)).exists(), "未走跨进程锁路径"
    assert "skill.review_waiver_publish" in fake.calls

    # ── 评估事件（service._emit_assessment_event）──
    events_path = tmp_path / "skills_assessment_events.jsonl"
    monkeypatch.setattr(la, "active_events_file", lambda: events_path)
    from agent.skills_mgmt.service import SkillsMgmtService
    svc = SkillsMgmtService.__new__(SkillsMgmtService)   # 不跑 __init__（免建真实目录）
    SkillsMgmtService._emit_assessment_event(svc, "skill-2", "auto", "ok", "摘要")
    ev_lines = _lines(events_path)
    assert len(ev_lines) == 1
    rec2 = json.loads(ev_lines[0])
    assert set(rec2) == {"ts", "kind", "skill_id", "verdict", "summary"}, rec2
    assert rec2["skill_id"] == "skill-2" and rec2["summary"] == "摘要"
    assert Path(la.jsonl_lock_path(events_path)).exists()
    assert "skill.assess.auto" in fake.calls

    stats = la.archive_stats()
    assert stats["append_locked"] == 2, stats
    assert stats["append_unlocked"] == 0, stats


def test_skills_shard_writer_degrades_visibly_when_lock_unavailable(tmp_path):
    """分片写入方拿不到锁 ⇒ **照样写**但计数（绝不静默丢审计记录）"""
    path = tmp_path / "skills_mgmt_review_audit.jsonl"
    holder = CrossProcessLock(la.jsonl_lock_path(path), name="holder")
    assert holder.try_lock() is True
    try:
        held = la.append_jsonl_locked(path, json.dumps({"ts": "", "event": "x"}),
                                      timeout=0.05)
        assert held is False, "拿不到锁时应返回 False（调用方可判降级）"
    finally:
        holder.release()

    assert len(_lines(path)) == 1, "降级也必须写入（不得丢）"
    stats = la.archive_stats()
    assert stats["append_unlocked"] == 1, stats
    assert stats["append_failures"] == 0, stats
