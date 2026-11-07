"""审计链「预留未落库」缺口：验签要能**区分**它和真篡改，并能从预留日志补写

【被测事故（本机实测，2026-11-07）】
    seq=82897（digest.shadow.observed / digestion_service）在
    data/audit/audit_chain.db.seqjournal 里、DB 里没有 ⇒ 其后每条记录都报
    「seq 不连续」，verify 的结论是 **TAMPERED**，与"真的删了一行"完全一样。
    根因是两条：① 入库失败被收敛放弃重试（DRAIN_MAX_ATTEMPTS）后不再重试；
    ② 启动自愈只看"日志链头 > DB 链头"，**中间空洞**永远修不掉。

【本文件的判据（三态，不放宽 ok）】
    - 缺口能在日志里找到哈希自洽的行 ⇒ status=pending_commit（可补写），
      但 ok 仍为 False（链确实还不完整）；
    - 日志里找不到 / 对不上 ⇒ status=tampered（真篡改，必须取证）；
    - 缺口**之后**的已入库记录必须逐条本地自洽（否则缺口就成了掩盖字段篡改的口子）。

【为什么用 reader 做断言、用独立 writer 做补写】
    writer 实例的 flush() 会先跑一次收敛（_recover_backlog）：那会把缺口**自动**补上，
    于是"能不能区分 pending / tampered"就永远测不到。reader 没有后台线程、也不收敛
    （与 CLI 的默认路径一致），断言才确定。
"""

from __future__ import annotations

import json

import pytest

from agent.audit.chain import (
    REASON_SEQ_GAP,
    STATUS_PENDING,
    STATUS_TAMPERED,
    AuditChain,
    ReadOnlyChainError,
    SeqJournal,
    reset_audit_chains,
)

pytestmark = [pytest.mark.unit, pytest.mark.p3]

_GAP_SEQ = 3
_N = 5


@pytest.fixture(autouse=True)
def _cleanup():
    reset_audit_chains()
    yield
    reset_audit_chains()


def _db(tmp_path) -> str:
    return str(tmp_path / "audit_chain.db")


def _roots(tmp_path) -> str:
    return str(tmp_path / "roots.jsonl")


def _writer(tmp_path, *, start_thread: bool) -> AuditChain:
    return AuditChain(_db(tmp_path), roots_path=_roots(tmp_path),
                      signing_enabled=False, auto_seal=False, lock_enabled=False,
                      auto_start_writer=start_thread)


def _seed(tmp_path) -> None:
    """真把 5 条写进 DB（writer 线程 + flush 屏障），然后关链释放写槽"""
    chain = _writer(tmp_path, start_thread=True)
    try:
        for i in range(_N):
            chain.append(f"gap.act{i}", "alice", f"skill:{i}")
        assert chain.flush(timeout=30.0) is True, "flush 屏障必须真的落盘"
        assert chain.count() == _N
    finally:
        chain.close()


def _reader(tmp_path) -> AuditChain:
    return AuditChain.reader(_db(tmp_path), roots_path=_roots(tmp_path),
                             signing_enabled=False)


def _exec(tmp_path, sql: str, params=()) -> None:
    import sqlite3
    conn = sqlite3.connect(_db(tmp_path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _rewrite_journal_row(tmp_path, seq: int, **changes) -> None:
    """把预留日志里某条 seq 的行改掉（模拟"日志被外部破坏/自洽性不成立"）"""
    path = _db(tmp_path) + ".seqjournal"
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if int(rec.get("seq") or 0) == seq:
                rec.update(changes)
            rows.append(json.dumps(rec, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def test_verify_marks_journal_backed_gap_as_pending(tmp_path):
    """缺口在日志里有且哈希自洽 ⇒ pending_commit（不是 tampered，但仍 not ok）"""
    _seed(tmp_path)
    _exec(tmp_path, "DELETE FROM audit_chain WHERE seq=?", (_GAP_SEQ,))
    v = _reader(tmp_path).verify_chain()
    assert not v.ok, "链确实还不完整，ok 不得放宽"
    assert v.reason == REASON_SEQ_GAP
    assert v.status == STATUS_PENDING
    assert v.pending_seqs == [_GAP_SEQ]
    assert "PENDING" in v.summary() and "--replay-journal" in v.summary()
    assert v.to_dict()["status"] == STATUS_PENDING


def test_verify_downgrades_to_tampered_when_journal_row_is_inconsistent(tmp_path):
    """日志里有这行但对不上（被改过）⇒ 仍然是 tampered：不得把篡改解释成待补写"""
    _seed(tmp_path)
    _rewrite_journal_row(tmp_path, _GAP_SEQ, actor="mallory")
    _exec(tmp_path, "DELETE FROM audit_chain WHERE seq=?", (_GAP_SEQ,))
    v = _reader(tmp_path).verify_chain()
    assert not v.ok and v.status == STATUS_TAMPERED
    assert v.pending_seqs == []
    assert "TAMPERED" in v.summary()


def test_verify_keeps_tampered_when_tail_record_was_edited(tmp_path):
    """缺口之后有字段篡改 ⇒ 必须仍判 tampered（防"缺口掩盖篡改"）"""
    _seed(tmp_path)
    _exec(tmp_path, "DELETE FROM audit_chain WHERE seq=?", (_GAP_SEQ,))
    _exec(tmp_path, "UPDATE audit_chain SET actor=? WHERE seq=?",
          ("mallory", _GAP_SEQ + 1))
    v = _reader(tmp_path).verify_chain()
    assert not v.ok and v.status == STATUS_TAMPERED
    assert v.pending_seqs == []


def test_replay_journal_pending_heals_the_gap(tmp_path):
    """补写后链必须完整（重放的是**日志里那条**，不是重新生成一条新的）"""
    _seed(tmp_path)
    _exec(tmp_path, "DELETE FROM audit_chain WHERE seq=?", (_GAP_SEQ,))
    chain = _writer(tmp_path, start_thread=False)
    try:
        report = chain.replay_journal_pending()
        assert report["requested"] == [_GAP_SEQ]
        assert report["replayed"] == [_GAP_SEQ], report["failed"]
        assert report["status"] == "ok"
        assert chain.verify_chain().ok is True
        assert chain.count() == _N
        entry = chain.get(_GAP_SEQ)
        assert entry is not None and entry.action == "gap.act2"
    finally:
        chain.close()


def test_replay_refuses_reader_role(tmp_path):
    """补写只走 writer（单写者纪律）：reader 必须显式拒绝，而不是偷偷写库"""
    _seed(tmp_path)
    with pytest.raises(ReadOnlyChainError):
        _reader(tmp_path).replay_journal_pending()


def test_startup_detects_middle_gap_and_self_heals(tmp_path):
    """**中间空洞**在下次启动时也要被收敛（旧实现只看"日志链头 > DB 链头"）"""
    _seed(tmp_path)
    _exec(tmp_path, "DELETE FROM audit_chain WHERE seq=?", (_GAP_SEQ,))
    revived = _writer(tmp_path, start_thread=False)
    try:
        assert revived._journal_needs_drain is True, \
            "启动没发现中间空洞（日志链头 == DB 链头时旧判据不会置标志）"
        revived._recover_backlog()
        assert revived._journal_needs_drain is False
        assert revived.verify_chain().ok is True
    finally:
        revived.close()


def test_rows_for_reads_without_enabled_and_drops_conflicting_duplicates(tmp_path):
    """rows_for 的判据：只读即可用（reader 的日志实例 enabled=False）、重复不一致即丢"""
    path = str(tmp_path / "j.jsonl")
    journal = SeqJournal(path, enabled=False)
    assert journal.rows_for([1]) == {}      # 文件不存在 ⇒ 空（不抛）
    writer = SeqJournal(path)
    try:
        for seq in (1, 2, 3):
            writer.append_row({"seq": seq, "self_hash": f"h{seq}"})
    finally:
        writer.close()
    got = journal.rows_for([1, 3])
    assert sorted(got) == [1, 3] and got[1]["self_hash"] == "h1"
    # 同 seq 两条不同记录 ⇒ 两条都不要（来路不明，不用于补链）
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"seq": 2, "self_hash": "other"}) + "\n")
    assert 2 not in journal.rows_for([2])


def test_cli_reports_pending_then_replays(tmp_path):
    """CLI：先 exit 3（PENDING）→ --replay-journal → exit 0（OK）"""
    from scripts.verify_audit_chain import main as cli_main

    _seed(tmp_path)
    _exec(tmp_path, "DELETE FROM audit_chain WHERE seq=?", (_GAP_SEQ,))

    code = cli_main(["--db", _db(tmp_path), "--roots", _roots(tmp_path)])
    assert code == 3, "只有预留未落库时必须退出 3（不是 1 = 篡改）"

    code = cli_main(["--db", _db(tmp_path), "--roots", _roots(tmp_path),
                     "--replay-journal"])
    assert code == 0
    assert _reader(tmp_path).verify_chain().ok is True
