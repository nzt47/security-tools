"""D1 读路径回归：`facade.recent()` 的 limit 下推 + 返回值语义不变

【被测缺陷（Q7 实测）】`agent/audit/facade.py` 的 `recent(limit=50)` 签名里有
`limit`，实现里却调 `chain.entries(**filters)` **不带 limit** → 全表读出后切片。
实测 72,289 行的库上单次 **1,239 ms**（`ORDER BY seq DESC LIMIT 50` 只要 1.4 ms）。
`chain.stats()` / `facade.snapshot()` / `chain.count()` / `chain.seq_range()` 同性质。

【本文件断言什么】
1. **语义不变**：`recent(n, **filters)` 与修复前的算法
   （`chain.entries(**filters)[-n:]`）**逐字等价**——顺序、条数、边界都一致；
2. **边界**：limit=0 / 负数 / 大于总数 / None；
3. **limit 真的下推到 SQL**（结构性断言：旧实现必红，不依赖计时）；
4. **"读得到刚写的"不丢**：尚未入库、只在预留日志里的记录仍能被 `recent()` 读到；
5. **快路径与全表口径一致**：`count()/seq_range()/stats()` 改为 SQL 聚合后数值不变。

【关于"最新的在前"】任务卡括号里写的是"最新的在前"，但**修复前**的真实语义是
**seq 升序、最新的一条在末尾**（`rows[-limit:]`），既有用例
`tests/unit/test_audit_facade.py::test_recent_returns_tail_in_seq_order`
断言的正是 `["act3", "act4"]`（最新的 `act4` 在**末尾**）。本卡的首要约束是
"不因修复而改变返回值语义"，故此处**保持 seq 升序**，并把该事实固化成断言
（`test_newest_entry_is_at_the_tail_not_the_head`）——若将来要改成"最新在前"，
必须同时改 UI/调用方，属独立变更。
"""
from __future__ import annotations

import json

import pytest

from agent.audit.chain import AuditChain, day_of_ts, reset_audit_chains
from agent.audit.facade import AuditFacade

pytestmark = [pytest.mark.unit, pytest.mark.p3]

_ROWS = 10


def _chain(tmp_path, *, auto_start_writer: bool, **kw) -> AuditChain:
    params = dict(roots_path=str(tmp_path / "roots.jsonl"), signing_enabled=False,
                  auto_seal=False, lock_enabled=False,
                  auto_start_writer=auto_start_writer)
    params.update(kw)
    return AuditChain(str(tmp_path / "audit_chain.db"), **params)


def _journal_row(seq: int, *, action: str = "d1.pending", source: str = "agent",
                 actor: str = "d1") -> dict:
    """一条"已分配 seq、已落预留日志、尚未入库"的合法记录（进程被杀窗口的形态）"""
    return {
        "seq": seq, "ts": "2026-01-%02dT00:00:00+00:00" % (1 + (seq % 9)),
        "actor": actor, "action": action, "subject": "p:%d" % seq,
        "payload_hash": "p" * 8, "prev_hash": "0" * 64, "self_hash": "h%04d" % seq,
        "source": source, "trace_id": "", "workspace_id": "", "schema_version": 1,
        "payload": json.dumps({"n": seq}, ensure_ascii=False),
    }


@pytest.fixture(autouse=True)
def _cleanup():
    reset_audit_chains()
    yield
    reset_audit_chains()


@pytest.fixture
def fc(tmp_path):
    """**已入库**的 10 条记录（writer 线程 + flush 屏障，读路径只走 DB）"""
    chain = _chain(tmp_path, auto_start_writer=True)
    facade = AuditFacade(chain=chain, enabled=True)
    for i in range(_ROWS):
        assert facade.record("act%d" % i, actor="a" if i % 2 else "b",
                             source="ui" if i % 3 == 0 else "agent") is not None
    assert chain.flush(timeout=5.0) is True
    try:
        yield facade, chain
    finally:
        chain.close(timeout=2.0)


@pytest.fixture
def jc(tmp_path):
    """**未入库**：记录只落在预留日志里（不启 writer 线程，直接注入日志行）"""
    chain = _chain(tmp_path, auto_start_writer=False)
    for seq in range(1, 31):
        chain._journal.append_row(_journal_row(seq))
    try:
        yield chain
    finally:
        chain.close(timeout=2.0)


# ════════════════════════════════════════════════════════════
#  1. 返回值语义（与修复前逐字一致）
# ════════════════════════════════════════════════════════════


class TestRecentSemantics:
    def test_returns_latest_n_in_seq_order(self, fc):
        facade, _ = fc
        got = facade.recent(3)
        assert [e.action for e in got] == ["act7", "act8", "act9"]
        assert [e.seq for e in got] == [8, 9, 10]

    def test_newest_entry_is_at_the_tail_not_the_head(self, fc):
        """固化修复前的真实语义：**seq 升序，最新的一条在末尾**（见模块 docstring）"""
        facade, chain = fc
        got = facade.recent(1)
        assert len(got) == 1
        assert got[0].seq == chain.last_entry().seq == _ROWS
        seqs = [e.seq for e in facade.recent(_ROWS)]
        assert seqs == sorted(seqs)

    def test_limit_greater_than_total_returns_everything(self, fc):
        facade, _ = fc
        assert [e.seq for e in facade.recent(999)] == list(range(1, _ROWS + 1))

    def test_limit_equal_to_total(self, fc):
        facade, _ = fc
        assert len(facade.recent(_ROWS)) == _ROWS

    def test_limit_zero_returns_empty(self, fc):
        """修复前 `limit=0` 落到 `if limit` 假分支 ⇒ **返回全表**（O(N) 陷阱）。

        本卡把它定义成"0 条"（与签名一致）；这是**唯一**一处刻意的语义收紧，
        已在 `facade.recent` 的 docstring 与 D1 报告里显式登记。
        """
        facade, _ = fc
        assert facade.recent(0) == []

    def test_limit_negative_returns_empty(self, fc):
        """修复前负数等价于 `rows[-(-n):]`（丢掉最旧的 n 条），属无意义行为"""
        facade, _ = fc
        assert facade.recent(-3) == []

    def test_limit_none_returns_all(self, fc):
        facade, chain = fc
        assert len(facade.recent(None)) == len(chain.entries()) == _ROWS

    def test_disabled_facade_returns_empty(self, tmp_path):
        assert AuditFacade(db_path=str(tmp_path / "x.db"), enabled=False).recent(5) == []

    def test_filters_pick_tail_of_filtered_set(self, fc):
        facade, chain = fc
        for n, filters in ((1, {}), (3, {}), (4, {"source": "ui"}),
                           (2, {"actor": "a"}), (5, {"source": "agent"})):
            legacy = chain.entries(**filters)[-n:]
            assert ([e.seq for e in facade.recent(n, **filters)]
                    == [e.seq for e in legacy])

    def test_day_filter_uses_tail_of_that_day(self, fc):
        facade, chain = fc
        day = day_of_ts(chain.last_entry().ts)
        legacy = chain.entries(day=day)[-2:]
        assert [e.seq for e in facade.recent(2, day=day)] == [e.seq for e in legacy]

    def test_recent_equals_legacy_slice_on_every_limit(self, fc):
        """**穷举** limit：与修复前算法逐字等价（本卡的核心不留回归断言）"""
        facade, chain = fc
        all_seqs = [e.seq for e in chain.entries()]
        for n in range(-2, _ROWS + 3):
            if n <= 0:
                assert facade.recent(n) == []       # 已登记的语义收紧
                continue
            legacy = chain.entries()[-int(n):]
            assert [e.seq for e in facade.recent(n)] == [e.seq for e in legacy], (
                "limit=%d 与修复前算法不一致" % n)
            assert [e.seq for e in facade.recent(n)] == all_seqs[-n:]


# ════════════════════════════════════════════════════════════
#  2. limit 必须真的下推到 SQL（结构性断言，不依赖计时）
# ════════════════════════════════════════════════════════════


class TestLimitPushedDownToSql:
    def test_recent_queries_desc_with_limit(self, fc, monkeypatch):
        facade, _ = fc
        calls = []
        orig = AuditChain._query_rows

        def spy(self, **kw):
            calls.append(kw)
            return orig(self, **kw)

        monkeypatch.setattr(AuditChain, "_query_rows", spy)
        got = facade.recent(3)
        assert [e.seq for e in got] == [8, 9, 10]
        # 旧实现：entries(**filters) -> _query_rows(limit=None, order_desc=False)
        assert calls, "recent() 没有走 _query_rows（实现路径被换掉了？）"
        assert calls[0]["limit"] == 3, "limit 未下推: %r" % (calls[0],)
        assert calls[0]["order_desc"] is True, "未按 seq 倒序取尾部: %r" % (calls[0],)

    def test_recent_never_requests_unbounded_read(self, fc, monkeypatch):
        facade, _ = fc
        calls = []
        orig = AuditChain._query_rows

        def spy(self, **kw):
            calls.append(kw)
            return orig(self, **kw)

        monkeypatch.setattr(AuditChain, "_query_rows", spy)
        facade.recent(50, source="ui")
        assert len(calls) == 1, "recent() 产生了多次查询（含全表？）: %r" % (calls,)
        assert calls[0]["limit"] == 50 and calls[0]["source"] == "ui"

    def test_tail_entries_zero_does_not_touch_db(self, fc, monkeypatch):
        _, chain = fc
        calls = []
        orig = AuditChain._query_rows
        monkeypatch.setattr(AuditChain, "_query_rows",
                            lambda self, **kw: (calls.append(kw), orig(self, **kw))[1])
        assert chain.tail_entries(0) == []
        assert chain.tail_entries(-1) == []
        assert calls == []


# ════════════════════════════════════════════════════════════
#  3. "读得到刚写的"：尚未入库的额外记录仍要合并（不能被修复掉）
# ════════════════════════════════════════════════════════════


class TestUncommittedStillVisible:
    def test_db_is_empty_but_journal_has_rows(self, jc):
        assert jc._db_head()[0] == 0, "本用例的前提是 DB 里一条都没有"
        assert jc.count() == 30

    def test_tail_entries_merges_journal(self, jc):
        assert [e.seq for e in jc.tail_entries(5)] == [26, 27, 28, 29, 30]

    def test_recent_sees_uncommitted(self, jc):
        facade = AuditFacade(chain=jc, enabled=True)
        got = facade.recent(3)
        assert [e.seq for e in got] == [28, 29, 30]
        assert got[-1].action == "d1.pending"

    def test_recent_with_filter_sees_uncommitted(self, jc):
        facade = AuditFacade(chain=jc, enabled=True)
        assert [e.seq for e in facade.recent(2, actor="d1")] == [29, 30]
        assert facade.recent(2, actor="nobody") == []

    def test_limit_larger_than_uncommitted(self, jc):
        assert [e.seq for e in jc.tail_entries(100)] == list(range(1, 31))

    def test_tail_and_entries_agree(self, jc):
        assert [e.seq for e in jc.tail_entries(7)] == [e.seq for e in jc.entries()][-7:]

    def test_stats_counts_uncommitted(self, jc):
        st = jc.stats(verify=False)
        assert st["total"] == 30
        assert st["by_source"] == {"agent": 30}
        assert st["by_actor"] == {"d1": 30}


# ════════════════════════════════════════════════════════════
#  4. 快路径（SQL 聚合）与全表口径一致
# ════════════════════════════════════════════════════════════


class TestFastPathsMatchFullScan:
    def test_count_matches_entries_len(self, fc):
        _, chain = fc
        assert chain.count() == len(chain.entries()) == _ROWS

    def test_count_with_filter_matches(self, fc):
        _, chain = fc
        for filters in ({"source": "ui"}, {"actor": "a"}, {"action": "act1"}):
            assert chain.count(**filters) == len(chain.entries(**filters))

    def test_seq_range_matches(self, fc):
        _, chain = fc
        entries = chain.entries()
        assert chain.seq_range() == (entries[0].seq, entries[-1].seq) == (1, _ROWS)

    def test_seq_range_empty_chain(self, tmp_path):
        chain = _chain(tmp_path, auto_start_writer=False)
        try:
            assert chain.seq_range() == (0, 0)
            assert chain.count() == 0
            assert chain.tail_entries(10) == []
        finally:
            chain.close(timeout=2.0)

    def test_stats_distribution_matches_full_scan(self, fc):
        _, chain = fc
        entries = chain.entries()
        st = chain.stats(verify=False)
        by_source = {}
        by_actor = {}
        for e in entries:
            by_source[e.source] = by_source.get(e.source, 0) + 1
            by_actor[e.actor] = by_actor.get(e.actor, 0) + 1
        assert st["total"] == len(entries)
        assert st["by_source"] == by_source
        assert st["by_actor"] == by_actor
        assert st["first_seq"] == 1 and st["last_seq"] == _ROWS
        assert st["head_self_hash"] == entries[-1].self_hash

    def test_snapshot_reports_same_totals(self, fc):
        facade, chain = fc
        snap = facade.snapshot()
        assert snap["chain"]["total"] == chain.count() == _ROWS
