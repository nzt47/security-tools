"""L1-a 回归：预留日志收敛窗口必须**随游标前进**（滞留 > 窗口时也能推进）

【被测缺陷（机制性、会复发）】
    ``chain._drain_journal`` 原实现每轮都 ``read_since(0, limit=5000)``。
    ``after_seq=0`` 使过滤恒真 ⇒ ``seq_journal.read_after`` 退化为
    "**升序排序后取前 5000 条**" ⇒ 每轮只看日志里**最老的** 5000 条。
    只要最老的一段里存在"读得出、却写不进 DB"的记录（或长期占低 seq 的在途记录），
    窗口就**永久钉死**：更高 seq 的滞留记录永远进不了收敛窗口
    ⇒ 永久滞留、可能静默丢失审计记录（压缩水位也只在"低 seq 已入库"时前进，不自愈）。

【为什么这里用"窗口 50 + 120 条滞留"而不是"5000 + 6000"】
    验证的是**同一个截断机制**：窗口大小是 ``DRAIN_JOURNAL_WINDOW`` 这个常量，
    缺陷与条数无关，只与"滞留条数 > 窗口"有关。把窗口 monkeypatch 成 50 并构造
    120 条滞留，等价于生产口径的"5000 窗口 + 6000 条滞留"（都是 1.2 倍窗口），
    却把 I/O 从 6000 行降到 120 行。断言口径保持在**机制层**：
    ① 返回集覆盖全部 120 个 seq（旧实现永远只有最老 50 个）；② 游标确实在前进；
    ③ 收敛最终归位（``_journal_needs_drain=False``）且全部入库。

【D7】这些用例只断言"收敛能推进"，不断言任何篡改检测被放宽：
    放弃重试的那条路径另有专门用例断言 **ERROR 留痕 + 记录仍在日志里**。
"""

from __future__ import annotations

import logging

import pytest

import agent.audit.chain as chain_mod
from agent.audit.chain import AuditChain, SeqJournal, reset_audit_chains

pytestmark = [pytest.mark.unit, pytest.mark.p3]

#: 收敛窗口（monkeypatch 后的值）与滞留条数：1.2 倍窗口，同生产口径的 5000/6000
_WINDOW = 50
_STRANDED = 120


@pytest.fixture(autouse=True)
def _cleanup():
    reset_audit_chains()
    yield
    reset_audit_chains()


def _journal_row(seq: int) -> dict:
    """一条**合法**的待入库行（模拟"进程被杀、已分配并落日志但未入库"）"""
    return {
        "seq": seq, "ts": "2026-01-01T00:00:00+00:00", "actor": "crash",
        "action": "stranded.write", "subject": "", "payload_hash": "p",
        "prev_hash": "0" * 64, "self_hash": f"h{seq}", "source": "agent",
        "trace_id": "", "workspace_id": "", "schema_version": 1, "payload": "{}",
    }


def _chain(tmp_path, **kw) -> AuditChain:
    """不启 writer 线程的链：收敛完全由用例显式驱动（时序确定，无 flake）"""
    params = dict(roots_path=str(tmp_path / "roots.jsonl"), signing_enabled=False,
                  auto_seal=False, auto_start_writer=False, lock_enabled=False)
    params.update(kw)
    return AuditChain(str(tmp_path / "audit_chain.db"), **params)


def test_seqjournal_read_after_is_a_real_cursor(tmp_path):
    """``SeqJournal.read_after``：``after_seq`` 是**游标**（返回 seq 最小的 limit 条）

    这是 L1-a 的**最小单元**证据：同一个 ``after_seq`` 只会返回同一段，
    推进 ``after_seq`` 才能拿到更靠后的一段。
    """
    journal = SeqJournal(str(tmp_path / "j.jsonl"))
    try:
        for seq in range(1, 11):
            journal.append_row({"seq": seq, "self_hash": f"h{seq}"})
        assert [r["seq"] for r in journal.read_after(0, limit=4)] == [1, 2, 3, 4]
        assert [r["seq"] for r in journal.read_after(4, limit=4)] == [5, 6, 7, 8]
        assert [r["seq"] for r in journal.read_after(8, limit=4)] == [9, 10]
        assert journal.read_after(10, limit=4) == []
        # 兼容别名逐字同义（历史调用点不受影响）
        assert ([r["seq"] for r in journal.read_since(4, limit=4)]
                == [r["seq"] for r in journal.read_after(4, limit=4)])
    finally:
        journal.close()


def test_drain_converges_when_stranded_exceeds_window(tmp_path, monkeypatch):
    """**滞留 120 条 > 窗口 50**：必须全部收敛（旧实现只覆盖最老 50 条）

    变异探针：把 ``_drain_journal`` 换回 ``read_since(0, limit=WINDOW)`` 的固定窗口
    写法，本用例立刻变红（``missing`` 为 51..120）。
    """
    monkeypatch.setattr(chain_mod, "DRAIN_JOURNAL_WINDOW", _WINDOW)
    chain = _chain(tmp_path)
    try:
        for seq in range(1, _STRANDED + 1):
            chain._journal.append_row(_journal_row(seq))
        chain._journal_needs_drain = True

        returned: list = []
        cursors: list = []
        rounds = 0
        while chain._journal_needs_drain and rounds < 200:
            rounds += 1
            cursors.append(chain._journal_drain_cursor)
            out = chain._drain_journal()
            returned.extend(int(e.seq) for e in out)
            if out:
                chain._write_to_db(out)

        seen = set(returned)
        missing = [s for s in range(1, _STRANDED + 1) if s not in seen]
        assert not missing, (
            f"滞留记录永远进不了收敛窗口（只覆盖最老一段）：missing={missing[:8]}"
            f"… 共 {len(missing)} 条；游标轨迹={cursors}")
        # 游标必须真的前进过（"窗口随游标移动"的直接证据）
        assert max(cursors) >= _WINDOW, f"游标从未越过第一个窗口：{cursors}"
        assert len(set(cursors)) >= 3, f"游标没有推进：{cursors}"
        # 收敛必须能归位（否则 writer 会一直空转、压缩也被饿死）
        assert chain._journal_needs_drain is False, "收敛未归位"
        assert chain.count() == _STRANDED, "滞留记录未全部入库"
        stats = chain.stats(verify=False)
        assert stats["journal_abandoned_count"] == 0, "可入库的记录被误判为写不进库"
        assert stats["journal_drain_stuck"] == 0
    finally:
        chain.close()


def test_drain_cursor_advances_even_when_window_needs_no_write(tmp_path, monkeypatch):
    """窗口内**全部已入库**时游标照样前进（否则就是"永远只看最老 N 条"的另一种形态）

    旧实现：``out`` 为空 ⇒ ``_journal_needs_drain=False`` ⇒ 收敛彻底停摆，
    排在窗口之外、尚未入库的记录**再也没有机会**被补写（静默丢失）。
    新实现：游标单调推进到日志尾并回卷，一轮完整周期覆盖整份日志。
    """
    monkeypatch.setattr(chain_mod, "DRAIN_JOURNAL_WINDOW", _WINDOW)
    chain = _chain(tmp_path)
    try:
        # 前 60 条**已入库**（模拟"最老的一段已经写进去了"）
        for seq in range(1, 61):
            chain._journal.append_row(_journal_row(seq))
        entries = [chain._entry_from_journal_row(_journal_row(seq))
                   for seq in range(1, 61)]
        chain._write_to_db(entries)
        # 后 60 条**只在日志里**（尚未入库）——它们排在窗口 50 之外
        for seq in range(61, 121):
            chain._journal.append_row(_journal_row(seq))
        chain._journal_needs_drain = True

        returned: list = []
        rounds = 0
        while chain._journal_needs_drain and rounds < 200:
            rounds += 1
            out = chain._drain_journal()
            returned.extend(int(e.seq) for e in out)
            if out:
                chain._write_to_db(out)
        assert sorted(set(returned)) == list(range(61, 121)), (
            "已入库的前缀占住窗口后，后面的滞留记录没能被推进到"
            f"：returned={sorted(set(returned))[:5]}…{sorted(set(returned))[-5:]}")
        assert chain.count() == 120
    finally:
        chain.close()


def test_drain_abandons_unwritable_seq_with_error_and_keeps_record(tmp_path, monkeypatch, caplog):
    """**写不进 DB 的记录**：放弃的是"收敛重试"，不是记录 —— 必须 ERROR 留痕

    构造：把 ``_write_to_db`` 打桩成"永远写不进 seq=1"（模拟约束冲突行）。
    断言：① 连续 ``DRAIN_MAX_ATTEMPTS`` 轮后不再重试（游标能越过它，收敛归位）；
    ② 有 ``logger.error``（绝不能静默）；③ 该行**仍在预留日志文件里**（可人工恢复）；
    ④ ``stats()`` 暴露计数与 seq。
    """
    monkeypatch.setattr(chain_mod, "DRAIN_JOURNAL_WINDOW", _WINDOW)
    chain = _chain(tmp_path)
    try:
        for seq in range(1, 11):
            chain._journal.append_row(_journal_row(seq))
        chain._journal_needs_drain = True

        real_write = chain._write_to_db

        def _write_dropping_one(records):
            keep = [r for r in records if int(r.seq) != 1]
            if keep:
                real_write(keep)
            # seq=1 被"写失败"：不进库，也不落 ring buffer（最坏情况）。
            # 【必须补这一步】真实 ``_write_to_db`` 在 ``finally`` 里对**整批**调
            # ``_clear_inflight``；打桩若漏掉它，该 seq 会永远留在"在途"集合里，
            # 收敛侧按设计跳过在途记录 —— 那测到的是打桩错误，不是放弃机制。
            chain._clear_inflight([r for r in records if int(r.seq) == 1])

        monkeypatch.setattr(chain, "_write_to_db", _write_dropping_one)

        with caplog.at_level(logging.ERROR, logger="agent.audit.chain"):
            rounds = 0
            while chain._journal_needs_drain and rounds < 200:
                rounds += 1
                out = chain._drain_journal()
                if out:
                    _write_dropping_one(out)

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "放弃重试必须记 ERROR 告警（否则就是静默丢弃）"
        assert "seq=1" in errors[0].getMessage()
        stats = chain.stats(verify=False)
        assert stats["journal_abandoned_count"] == 1, stats["journal_abandoned_seqs"]
        assert stats["journal_abandoned_seqs"] == [1]
        assert chain._journal_needs_drain is False, "放弃后收敛仍未归位（会空转）"
        # 记录仍在日志里（压缩水位越不过它 ⇒ 压缩不会把它删掉）
        assert [r["seq"] for r in chain._journal.read_after(0, limit=100)] == list(range(1, 11))
        assert 1 not in {int(e.seq) for e in chain.entries()}, "被打桩的行不该进库"
        assert 2 in {int(e.seq) for e in chain.entries()}, "其余记录必须照常收敛"
    finally:
        chain.close()


def test_drain_does_not_abandon_when_db_unavailable(tmp_path, monkeypatch, caplog):
    """DB 不可用（查询失败）时**不得**计入放弃：那是瞬时状态，不是"写不进库"

    否则一次 DB 抖动就会把成批滞留记录永久标成不可入库并停止重试 —— 新的静默点。
    """
    monkeypatch.setattr(chain_mod, "DRAIN_JOURNAL_WINDOW", _WINDOW)
    chain = _chain(tmp_path)
    try:
        for seq in range(1, 6):
            chain._journal.append_row(_journal_row(seq))
        chain._db_available = False
        chain._journal_needs_drain = True
        with caplog.at_level(logging.ERROR, logger="agent.audit.chain"):
            for _ in range(5):
                chain._drain_journal()
        assert chain.stats(verify=False)["journal_abandoned_count"] == 0, \
            "DB 不可用被误判为记录写不进库（瞬时状态被当成永久失败）"
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    finally:
        chain.close()


def test_recover_backlog_covers_more_than_one_window(tmp_path, monkeypatch):
    """``flush()`` 走的 ``_recover_backlog`` 必须覆盖**多窗口**（持久化屏障语义）

    一次只推进一个窗口的话，"滞留 > 窗口"时 ``flush()`` 会在没收敛完的情况下返回。
    """
    monkeypatch.setattr(chain_mod, "DRAIN_JOURNAL_WINDOW", _WINDOW)
    chain = _chain(tmp_path)
    try:
        for seq in range(1, 121):
            chain._journal.append_row(_journal_row(seq))
        chain._journal_needs_drain = True
        assert chain._recover_backlog() == 120
        assert chain.count() == 120
        assert chain._journal_needs_drain is False
    finally:
        chain.close()
