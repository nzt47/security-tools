"""TASK-S2-02 链式审计核心单测（`agent/audit/chain.py`）

覆盖：哈希公式（§3.5）/ 记录模型 / 单写者与并发 / seq 单调与重启续链 /
验签与篡改定位 / Merkle 树与成员证明 / 每日根与重放 / 签名（ed25519 + 降级）/
append-only 不变量 / append 性能。
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
import time

import pytest

from agent.audit import chain as chain_mod
from agent.audit.chain import (
    DEFAULT_DB_PATH,
    EMPTY_MERKLE_ROOT,
    GENESIS_PREV_HASH,
    REASON_PAYLOAD_HASH,
    REASON_PREV_HASH,
    REASON_SEQ_GAP,
    REASON_SELF_HASH,
    AuditChain,
    AuditEntry,
    AuditEntryError,
    DailyRoot,
    ReadOnlyChainError,
    RootsSigner,
    SingleWriterViolationError,
    build_entry,
    canonical_record_json,
    compute_payload_hash,
    compute_self_hash,
    get_audit_chain,
    merkle_proof,
    merkle_root,
    reset_audit_chains,
    self_hash_formula,
    sha256_hex,
    verify_chain as run_verify_chain,
    verify_merkle_proof as run_verify_merkle_proof,
)


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def shared_key(tmp_path_factory):
    """会话级共享的 ed25519 私钥路径

    每用例新建台账时若各自生成密钥，会为每次 `AuditChain` 构造触发一次 OpenSSL 密钥
    生成（native 开销 + 文件落盘）；CI Shard 内多文件同进程运行时会放大 native 压力
    （曾与 pyarrow/pandas 原生导入争资源导致 access violation）。故全模块共用一把密钥。
    """
    d = tmp_path_factory.mktemp("audit_keys")
    return str(d / "audit_signing_key.pem")


@pytest.fixture
def paths(tmp_path, shared_key):
    return {
        "db": str(tmp_path / "audit_chain.db"),
        "roots": str(tmp_path / "daily_roots.jsonl"),
        "key": shared_key,
    }


@pytest.fixture
def chain(paths):
    reset_audit_chains()
    c = AuditChain(paths["db"], roots_path=paths["roots"],
                   signing_key_path=paths["key"], auto_seal=False)
    yield c
    try:
        c.close(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass
    reset_audit_chains()


def _tamper(db_path: str, sql: str, params: tuple = ()) -> None:
    """直接改库（模拟攻击者绕过审计入口篡改台账）"""
    conn = sqlite3.connect(db_path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _entry(seq: int, **kw) -> AuditEntry:
    base = dict(seq=seq, ts=f"2026-09-10T00:00:{seq:02d}.000000+00:00",
                actor="a", action="act", subject="s", prev_hash=GENESIS_PREV_HASH)
    base.update(kw)
    return build_entry(**base)


def _chain_of(n: int) -> list:
    """自建 n 条合法链（不落库）"""
    out: list = []
    prev = GENESIS_PREV_HASH
    for i in range(1, n + 1):
        e = _entry(i, prev_hash=prev)
        out.append(e)
        prev = e.self_hash
    return out


# ════════════════════════════════════════════════════════════
#  1. §3.5 哈希公式
# ════════════════════════════════════════════════════════════


class TestHashFormula:
    def test_self_hash_formula_field_order_and_separator(self):
        """§3.5：self_hash = sha256(seq+ts+actor+action+subject+payload_hash+prev_hash)"""
        value = self_hash_formula(seq=7, ts="T", actor="A", action="X", subject="S",
                                  payload_hash="P", prev_hash="V")
        assert value == "7|T|A|X|S|P|V"

    def test_compute_self_hash_matches_manual_sha256(self):
        expect = sha256_hex("7|T|A|X|S|P|V")
        assert compute_self_hash(seq=7, ts="T", actor="A", action="X", subject="S",
                                 payload_hash="P", prev_hash="V") == expect

    def test_self_hash_is_64_hex(self):
        h = compute_self_hash(seq=1, ts="t", actor="a", action="x", subject="",
                              payload_hash="p", prev_hash=GENESIS_PREV_HASH)
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)

    def test_genesis_prev_hash_is_64_zeros(self):
        assert GENESIS_PREV_HASH == "0" * 64

    def test_payload_hash_is_key_order_insensitive(self):
        a = compute_payload_hash(seq=1, ts="t", actor="a", action="x", subject="s",
                                 payload={"b": 1, "a": 2})
        b = compute_payload_hash(seq=1, ts="t", actor="a", action="x", subject="s",
                                 payload={"a": 2, "b": 1})
        assert a == b

    def test_payload_hash_changes_with_payload_value(self):
        a = compute_payload_hash(seq=1, ts="t", actor="a", action="x", subject="s",
                                 payload={"k": 1})
        b = compute_payload_hash(seq=1, ts="t", actor="a", action="x", subject="s",
                                 payload={"k": 2})
        assert a != b

    def test_payload_hash_binds_metadata_fields(self):
        """元数据（source/trace_id/workspace_id/schema_version）一并受绑定"""
        base = compute_payload_hash(seq=1, ts="t", actor="a", action="x", subject="s")
        assert base != compute_payload_hash(seq=1, ts="t", actor="a", action="x",
                                            subject="s", source="ui")
        assert base != compute_payload_hash(seq=1, ts="t", actor="a", action="x",
                                            subject="s", trace_id="t1")
        assert base != compute_payload_hash(seq=1, ts="t", actor="a", action="x",
                                            subject="s", workspace_id="w1")
        assert base != compute_payload_hash(seq=1, ts="t", actor="a", action="x",
                                            subject="s", schema_version=2)

    def test_canonical_record_json_is_sorted_and_compact(self):
        text = canonical_record_json(seq=1, ts="t", actor="a", action="x", subject="s",
                                     source="agent", trace_id="", workspace_id="",
                                     schema_version=1, payload={"b": 1, "a": 2})
        assert '"payload":{"a":2,"b":1}' in text
        assert ", " not in text

    def test_build_entry_sets_both_hashes_consistently(self):
        e = _entry(3)
        assert e.payload_hash == e.recompute_payload_hash()
        assert e.self_hash == e.recompute_self_hash()

    def test_entry_hash_differs_on_any_signed_field(self):
        e1 = _entry(3)
        for field, value in (("ts", "2026-01-01T00:00:00.000000+00:00"),
                             ("actor", "other"), ("action", "other"),
                             ("subject", "other"), ("source", "ui"),
                             ("trace_id", "t9"), ("workspace_id", "w9")):
            e2 = _entry(3, **{field: value})
            assert e2.self_hash != e1.self_hash or e2.payload_hash != e1.payload_hash


# ════════════════════════════════════════════════════════════
#  2. 记录模型
# ════════════════════════════════════════════════════════════


class TestAuditEntry:
    def test_validate_ok(self):
        assert _entry(1).validate() == []

    def test_validate_flags_bad_seq_and_empty_action(self):
        assert "seq 必须为正整数" in _entry(-1).validate()
        assert "action 不能为空" in _entry(1, action="").validate()

    def test_non_dict_payload_wrapped(self):
        e = AuditEntry(seq=1, ts="t", actor="a", action="x", payload=[1, 2])
        assert e.payload == {"value": [1, 2]}

    def test_to_dict_from_dict_roundtrip(self):
        e = _entry(5, payload={"k": "v"})
        again = AuditEntry.from_dict(e.to_dict())
        assert again.to_dict() == e.to_dict()

    def test_from_dict_parses_payload_json_string(self):
        e = AuditEntry.from_dict({"seq": 1, "ts": "t", "actor": "a", "action": "x",
                                  "payload": '{"k":1}'})
        assert e.payload == {"k": 1}

    def test_from_dict_tolerates_corrupt_payload(self):
        e = AuditEntry.from_dict({"seq": 1, "ts": "t", "actor": "a", "action": "x",
                                  "payload": "{not json"})
        assert e.payload == {"_raw": "{not json"}

    def test_to_public_dict_excludes_payload(self):
        d = _entry(1, payload={"secret": "x"}).to_public_dict()
        assert "payload" not in d and d["seq"] == 1

    def test_row_values_and_from_row_roundtrip(self, chain):
        e = chain.append("act", "alice", "skill:s", {"k": "v"}, source="ui")
        chain.flush()
        with chain._connect() as conn:
            row = conn.execute("SELECT * FROM audit_chain WHERE seq=?",
                               (e.seq,)).fetchone()
        again = AuditEntry.from_row(row)
        assert again.payload_hash == e.payload_hash
        assert again.self_hash == e.self_hash
        assert again.payload == e.payload
        assert again.id > 0

    def test_payload_json_roundtrip_preserves_hash(self, chain):
        """payload 以规范化 JSON 落库，读回重新规范化恒等（哈希稳定）"""
        e = chain.append("act", "a", "s", {"nested": {"b": [1, 2, {"c": "中文"}]}})
        chain.flush()
        got = chain.get(e.seq)
        assert got.recompute_payload_hash() == got.payload_hash


# ════════════════════════════════════════════════════════════
#  3. 链式追加 / seq 单调 / 重启续链
# ════════════════════════════════════════════════════════════


class TestAppendAndSeq:
    def test_first_entry_links_to_genesis(self, chain):
        e = chain.append("act", "a")
        assert e.seq == 1
        assert e.prev_hash == GENESIS_PREV_HASH

    def test_seq_monotonic_and_prev_hash_links(self, chain):
        entries = [chain.append(f"act{i}", "a") for i in range(5)]
        assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
        for prev, cur in zip(entries, entries[1:]):
            assert cur.prev_hash == prev.self_hash

    def test_next_seq_and_last_hash_properties(self, chain):
        assert chain.next_seq == 1
        e = chain.append("act", "a")
        assert chain.next_seq == 2
        assert chain.last_hash == e.self_hash

    def test_empty_action_rejected(self, chain):
        with pytest.raises(AuditEntryError):
            chain.append("", "a")

    def test_invalid_source_rejected(self, chain):
        with pytest.raises(AuditEntryError):
            chain.append("act", "a", source="hacker")

    def test_append_after_close_rejected(self, chain):
        chain.close()
        with pytest.raises(AuditEntryError):
            chain.append("act", "a")

    def test_reader_role_cannot_append(self, paths):
        c = AuditChain.reader(paths["db"], roots_path=paths["roots"])
        with pytest.raises(ReadOnlyChainError):
            c.append("act", "a")

    def test_restart_continues_seq_and_head_hash(self, paths):
        c1 = AuditChain(paths["db"], roots_path=paths["roots"],
                        signing_key_path=paths["key"], auto_seal=False)
        for i in range(3):
            c1.append(f"act{i}", "a")
        c1.flush()
        head = c1.last_hash
        c1.close()
        reset_audit_chains()

        c2 = AuditChain(paths["db"], roots_path=paths["roots"],
                        signing_key_path=paths["key"], auto_seal=False)
        assert c2.next_seq == 4
        assert c2.last_hash == head
        e = c2.append("act4", "a")
        assert e.seq == 4 and e.prev_hash == head
        c2.flush()
        assert c2.verify_chain().ok
        c2.close()
        reset_audit_chains()

    def test_persisted_rows_survive_reopen(self, paths):
        c1 = AuditChain(paths["db"], roots_path=paths["roots"],
                        signing_key_path=paths["key"], auto_seal=False)
        c1.append("act", "alice", "skill:x", {"k": 1}, source="ui")
        c1.flush()
        c1.close()
        reset_audit_chains()
        c2 = AuditChain.reader(paths["db"], roots_path=paths["roots"])
        got = c2.entries()
        assert len(got) == 1 and got[0].actor == "alice" and got[0].source == "ui"

    def test_ts_defaults_to_utc_iso(self, chain):
        e = chain.append("act", "a")
        assert e.ts.endswith("+00:00") and e.ts[4] == "-"

    def test_explicit_ts_respected(self, chain):
        e = chain.append("act", "a", ts="2026-01-02T03:04:05.000000+00:00")
        assert e.ts.startswith("2026-01-02")

    def test_chain_flush_makes_rows_visible(self, chain):
        for i in range(10):
            chain.append(f"a{i}", "x")
        assert chain.flush(timeout=5.0) is True
        assert chain.count() == 10


# ════════════════════════════════════════════════════════════
#  4. 单写者与并发
# ════════════════════════════════════════════════════════════


class TestSingleWriter:
    def test_second_writer_same_path_raises(self, paths, chain):
        with pytest.raises(SingleWriterViolationError):
            AuditChain(paths["db"], roots_path=paths["roots"])

    def test_reader_does_not_take_writer_slot(self, paths, chain):
        r = AuditChain.reader(paths["db"], roots_path=paths["roots"])
        assert r.role == "reader"
        assert chain_mod.active_writers().get(os.path.abspath(paths["db"]))

    def test_get_audit_chain_returns_singleton(self, paths):
        reset_audit_chains()
        a = get_audit_chain(paths["db"], roots_path=paths["roots"],
                            signing_key_path=paths["key"])
        b = get_audit_chain(paths["db"])
        assert a is b
        reset_audit_chains()

    def test_close_releases_writer_slot(self, paths):
        c = AuditChain(paths["db"], roots_path=paths["roots"])
        c.close()
        c2 = AuditChain(paths["db"], roots_path=paths["roots"])
        assert c2.role == "writer"
        c2.close()

    def test_concurrent_appends_unique_seqs_no_race(self, chain):
        """并发写：无重复 seq / 无缺口 / 链自洽

        规模刻意克制（4 线程 × 10 条）：本用例在 CI 的 6-shard 单元测试 job 中与其它
        线程密集用例同进程运行，过度加压会把同 shard 的既有并发用例推过 60s 超时
        （CI Shard6 曾因 `test_reflection_concurrency` 超时失败）。规模下的不变量
        （唯一 seq / 连续 / 前驱链）与大规模完全一致；演示脚本另有 200 条批量实测。
        """
        n_threads, per = 4, 10
        barrier = threading.Barrier(n_threads)
        errors: list = []

        def worker(idx: int) -> None:
            try:
                barrier.wait(timeout=5)
                for i in range(per):
                    chain.append(f"t{idx}.a{i}", f"actor{idx}",
                                 payload={"t": idx, "i": i})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        chain.flush(timeout=10.0)
        entries = chain.entries()
        seqs = [e.seq for e in entries]
        assert len(seqs) == n_threads * per
        assert len(set(seqs)) == len(seqs)
        assert seqs == list(range(1, n_threads * per + 1))
        assert chain.verify_chain().ok

    def test_concurrent_appends_keep_actor_attribution(self, chain):
        def worker(idx: int) -> None:
            for i in range(10):
                chain.append("act", f"actor{idx}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        chain.flush(timeout=10.0)
        actors = sorted({e.actor for e in chain.entries()})
        assert actors == ["actor0", "actor1", "actor2", "actor3"]


# ════════════════════════════════════════════════════════════
#  5. 读取接口
# ════════════════════════════════════════════════════════════


class TestReads:
    def _seed(self, chain):
        chain.append("skill.delete", "admin", "skill:a", source="ui",
                     ts="2026-09-09T10:00:00.000000+00:00")
        chain.append("approval.approve", "reviewer", "skill:b", source="agent",
                     ts="2026-09-09T11:00:00.000000+00:00")
        chain.append("config.write", "admin", "ui:/api/config", source="ui",
                     ts="2026-09-10T09:00:00.000000+00:00")
        chain.flush()

    def test_filter_by_source(self, chain):
        self._seed(chain)
        assert [e.action for e in chain.entries(source="ui")] == \
            ["skill.delete", "config.write"]
        assert [e.action for e in chain.entries(source="agent")] == ["approval.approve"]

    def test_filter_by_action_actor_day(self, chain):
        self._seed(chain)
        assert len(chain.entries(action="skill.delete")) == 1
        assert len(chain.entries(actor="admin")) == 2
        assert len(chain.entries(day="2026-09-09")) == 2

    def test_filter_by_seq_range_and_limit(self, chain):
        self._seed(chain)
        assert [e.seq for e in chain.entries(start_seq=2, end_seq=3)] == [2, 3]
        assert [e.seq for e in chain.entries(limit=2)] == [1, 2]

    def test_get_and_last_entry(self, chain):
        self._seed(chain)
        assert chain.get(2).action == "approval.approve"
        assert chain.get(99) is None
        assert chain.last_entry().seq == 3

    def test_seq_range_and_count(self, chain):
        assert chain.seq_range() == (0, 0)
        self._seed(chain)
        assert chain.seq_range() == (1, 3)
        assert chain.count() == 3

    def test_iter_entries_batches(self, chain):
        for i in range(25):
            chain.append(f"a{i}", "x")
        chain.flush()
        assert len(list(chain.iter_entries(batch=10))) == 25

    def test_entries_of_day(self, chain):
        self._seed(chain)
        assert [e.action for e in chain.entries_of_day("2026-09-09")] == \
            ["skill.delete", "approval.approve"]

    def test_chain_head_summary(self, chain):
        self._seed(chain)
        head = chain.chain_head()
        assert head["count"] == 3 and head["last_seq"] == 3
        assert len(head["head_self_hash"]) == 64

    def test_stats_shape_and_verification(self, chain):
        self._seed(chain)
        st = chain.stats()
        assert st["total"] == 3
        assert st["by_source"] == {"ui": 2, "agent": 1}
        assert st["chain_ok"] is True and st["append_only"] is True


# ════════════════════════════════════════════════════════════
#  6. 验签与篡改定位
# ════════════════════════════════════════════════════════════


class TestVerifyChain:
    def test_empty_chain_is_ok(self):
        v = run_verify_chain([])
        assert v.ok and v.reason == "empty"

    def test_valid_chain_ok(self):
        v = run_verify_chain(_chain_of(5))
        assert v.ok and v.checked == 5 and v.first_bad_seq is None

    def test_field_tamper_detected_at_that_seq(self):
        entries = _chain_of(6)
        entries[2].actor = "mallory"          # 篡改第 3 条
        v = run_verify_chain(entries)
        assert not v.ok and v.first_bad_seq == 3

    def test_payload_tamper_detected(self):
        entries = _chain_of(4)
        entries[1].payload = {"injected": True}
        v = run_verify_chain(entries)
        assert not v.ok and v.first_bad_seq == 2 and v.reason == REASON_PAYLOAD_HASH

    def test_self_hash_tamper_detected(self):
        entries = _chain_of(4)
        entries[1].self_hash = sha256_hex("forged")
        v = run_verify_chain(entries)
        assert not v.ok and v.first_bad_seq == 2
        assert v.reason in (REASON_SELF_HASH, REASON_PREV_HASH)

    def test_prev_hash_break_detected(self):
        entries = _chain_of(4)
        entries[2].prev_hash = GENESIS_PREV_HASH
        v = run_verify_chain(entries)
        assert not v.ok and v.first_bad_seq == 3 and v.reason == REASON_PREV_HASH

    def test_seq_gap_detected(self):
        entries = _chain_of(5)
        del entries[2]
        v = run_verify_chain(entries)
        assert not v.ok and v.reason == REASON_SEQ_GAP and v.first_bad_seq == 4

    def test_all_subsequent_records_fail_after_tamper(self):
        """改中间一条 → 该条后续**全部**失败（哈希前向传播）"""
        entries = _chain_of(8)
        entries[2].subject = "tampered"
        v = run_verify_chain(entries)
        assert v.first_bad_seq == 3
        assert [b["seq"] for b in v.bad_seqs] == [3, 4, 5, 6, 7, 8]
        assert v.checked == 2   # 仅前 2 条通过

    def test_tamper_detected_from_db(self, chain):
        for i in range(5):
            chain.append(f"act{i}", "alice")
        chain.flush()
        _tamper(chain.db_path, "UPDATE audit_chain SET actor='mallory' WHERE seq=3")
        v = chain.verify_chain()
        assert not v.ok and v.first_bad_seq == 3

    def test_delete_middle_row_detected(self, chain):
        for i in range(5):
            chain.append(f"act{i}", "a")
        chain.flush()
        _tamper(chain.db_path, "DELETE FROM audit_chain WHERE seq=3")
        v = chain.verify_chain()
        assert not v.ok and v.reason == REASON_SEQ_GAP

    def test_delete_first_row_detected_via_genesis_link(self, chain):
        for i in range(4):
            chain.append(f"act{i}", "a")
        chain.flush()
        _tamper(chain.db_path, "DELETE FROM audit_chain WHERE seq=1")
        v = chain.verify_chain()
        assert not v.ok and v.reason == REASON_PREV_HASH

    def test_anchor_verification_from_mid_seq(self, chain):
        for i in range(6):
            chain.append(f"act{i}", "a")
        chain.flush()
        v = chain.verify_chain(start_seq=4)
        assert v.ok and v.anchor_seq == 4 and v.checked == 3

    def test_anchor_verification_detects_tamper_inside_range(self, chain):
        for i in range(6):
            chain.append(f"act{i}", "a")
        chain.flush()
        _tamper(chain.db_path, "UPDATE audit_chain SET subject='x' WHERE seq=5")
        v = chain.verify_chain(start_seq=4, end_seq=6)
        assert not v.ok and v.first_bad_seq == 5

    def test_end_seq_limit(self, chain):
        for i in range(5):
            chain.append(f"act{i}", "a")
        chain.flush()
        _tamper(chain.db_path, "UPDATE audit_chain SET actor='m' WHERE seq=5")
        assert chain.verify_chain(end_seq=4).ok is True
        assert chain.verify_chain().ok is False

    def test_verification_serializable(self):
        v = run_verify_chain(_chain_of(2))
        d = v.to_dict()
        assert d["ok"] is True and d["checked"] == 2
        assert "OK" in v.summary()

    def test_tampered_summary_mentions_injected_seq(self):
        entries = _chain_of(3)
        entries[1].action = "forged"
        text = run_verify_chain(entries).summary()
        assert "seq=2" in text and "TAMPERED" in text


# ════════════════════════════════════════════════════════════
#  7. Merkle 树
# ════════════════════════════════════════════════════════════


class TestMerkle:
    def test_empty_root_is_sha256_of_empty(self):
        assert merkle_root([]) == EMPTY_MERKLE_ROOT
        assert merkle_root([]) == sha256_hex("")

    def test_single_leaf_root_is_leaf(self):
        h = sha256_hex("leaf")
        assert merkle_root([h]) == h

    def test_two_leaves_pairs(self):
        a, b = sha256_hex("a"), sha256_hex("b")
        assert merkle_root([a, b]) == sha256_hex(a + b)

    def test_odd_leaves_promote_last(self):
        a, b, c = (sha256_hex(x) for x in "abc")
        assert merkle_root([a, b, c]) == merkle_root([sha256_hex(a + b), c])

    def test_root_is_deterministic(self):
        leaves = [sha256_hex(str(i)) for i in range(7)]
        assert merkle_root(leaves) == merkle_root(list(leaves))

    def test_order_matters(self):
        a, b = sha256_hex("a"), sha256_hex("b")
        assert merkle_root([a, b]) != merkle_root([b, a])

    def test_root_changes_on_leaf_change(self):
        leaves = [sha256_hex(str(i)) for i in range(4)]
        tampered = list(leaves)
        tampered[2] = sha256_hex("evil")
        assert merkle_root(leaves) != merkle_root(tampered)

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 9, 16, 17])
    def test_proof_verifies_for_every_leaf(self, n):
        leaves = [sha256_hex(f"leaf{i}") for i in range(n)]
        root = merkle_root(leaves)
        for i, leaf in enumerate(leaves):
            proof = merkle_proof(leaves, i)
            assert run_verify_merkle_proof(leaf, proof, root) is True

    def test_wrong_leaf_fails_proof(self):
        leaves = [sha256_hex(f"l{i}") for i in range(5)]
        proof = merkle_proof(leaves, 1)
        assert run_verify_merkle_proof(sha256_hex("evil"), proof, merkle_root(leaves)) is False

    def test_proof_index_out_of_range(self):
        with pytest.raises(IndexError):
            merkle_proof([sha256_hex("a")], 3)


# ════════════════════════════════════════════════════════════
#  8. 每日 Merkle 根与重放
# ════════════════════════════════════════════════════════════


class TestDailyRoot:
    def _seed_day(self, chain, day="2026-09-09", n=4):
        for i in range(n):
            chain.append(f"act{i}", "alice", f"s{i}",
                         ts=f"{day}T1{i}:00:00.000000+00:00")
        chain.flush()

    def test_daily_root_matches_entries(self, chain):
        self._seed_day(chain)
        root = chain.daily_merkle_root("2026-09-09")
        leaves = [e.self_hash for e in chain.entries(day="2026-09-09")]
        assert root.root_hash == merkle_root(leaves)
        assert root.leaf_count == 4
        assert root.first_seq == 1 and root.last_seq == 4

    def test_daily_root_written_to_file(self, chain, paths):
        self._seed_day(chain)
        chain.daily_merkle_root("2026-09-09")
        assert os.path.exists(paths["roots"])
        rec = json.loads(pathlib.Path(paths["roots"]).read_text(encoding="utf-8").strip())
        assert rec["date"] == "2026-09-09"
        assert rec["root_hash"] == chain.get_daily_root("2026-09-09").root_hash
        assert rec["entry_hash"]  # 外层链

    def test_daily_root_replay_ok(self, chain):
        self._seed_day(chain)
        chain.daily_merkle_root("2026-09-09")
        rep = chain.verify_daily_root("2026-09-09")
        assert rep.ok and rep.entries_verified == 4

    def test_daily_root_idempotent(self, chain):
        self._seed_day(chain)
        r1 = chain.daily_merkle_root("2026-09-09")
        r2 = chain.daily_merkle_root("2026-09-09")
        assert r1.root_hash == r2.root_hash
        assert len(chain.read_daily_roots()) == 1

    def test_daily_root_force_appends_new_record(self, chain):
        self._seed_day(chain)
        chain.daily_merkle_root("2026-09-09")
        chain.daily_merkle_root("2026-09-09", force=True)
        assert len(chain.read_daily_roots()) == 2

    def test_daily_root_no_write_mode(self, chain):
        self._seed_day(chain)
        r = chain.daily_merkle_root("2026-09-09", write=False)
        assert r.root_hash and chain.read_daily_roots() == []

    def test_empty_day_root_is_attested(self, chain):
        r = chain.daily_merkle_root("2026-09-01")
        assert r.leaf_count == 0 and r.root_hash == EMPTY_MERKLE_ROOT

    def test_root_detects_entry_tamper_after_seal(self, chain):
        self._seed_day(chain)
        chain.daily_merkle_root("2026-09-09")
        _tamper(chain.db_path, "UPDATE audit_chain SET actor='mallory' WHERE seq=2")
        rep = chain.verify_daily_root("2026-09-09")
        assert not rep.ok and rep.reason == "entry_hash_mismatch"

    def test_root_detects_leaves_change(self, chain):
        """封印区间内被改 → 检出；封印后追加当日新记录 → 不改写历史（前缀快照语义）"""
        self._seed_day(chain)
        root = chain.daily_merkle_root("2026-09-09")
        chain.append("late.act", "a", ts="2026-09-09T23:59:59.000000+00:00")
        chain.flush()
        # 封印是对当时链头的前缀快照：新增不产生误报
        assert chain.verify_daily_root("2026-09-09").ok is True
        assert root.last_seq == 4
        # 区间内被删 → 必报（叶子数/根哈希双检）
        _tamper(chain.db_path, "DELETE FROM audit_chain WHERE seq=2")
        rep = chain.verify_daily_root("2026-09-09")
        assert not rep.ok and rep.reason in ("root_hash_mismatch",
                                             "leaf_count_mismatch")

    def test_root_covers_exact_sealed_range(self, chain):
        """根记录含 first_seq/last_seq：重放只覆盖封印区间"""
        self._seed_day(chain, n=3)
        root = chain.daily_merkle_root("2026-09-09")
        assert (root.first_seq, root.last_seq) == (1, 3)
        self._seed_day(chain, day="2026-09-10", n=2)
        rep = chain.verify_daily_root("2026-09-09")
        assert rep.ok and rep.entries_verified == 3

    def test_root_detects_recorded_root_tamper(self, chain, paths):
        self._seed_day(chain)
        chain.daily_merkle_root("2026-09-09")
        os.chmod(paths["roots"], 0o644)
        text = pathlib.Path(paths["roots"]).read_text(encoding="utf-8")
        rec = json.loads(text.strip())
        rec["root_hash"] = sha256_hex("forged")
        pathlib.Path(paths["roots"]).write_text(
            json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        rep = chain.verify_daily_root("2026-09-09")
        assert not rep.ok and rep.reason in ("root_hash_mismatch", "root_chain_broken")

    def test_root_chain_detects_deleted_day_record(self, chain, paths):
        self._seed_day(chain, day="2026-09-09")
        chain.daily_merkle_root("2026-09-09")
        self._seed_day(chain, day="2026-09-10")
        chain.daily_merkle_root("2026-09-10")
        os.chmod(paths["roots"], 0o644)
        lines = pathlib.Path(paths["roots"]).read_text(encoding="utf-8").splitlines()
        pathlib.Path(paths["roots"]).write_text(lines[-1] + "\n", encoding="utf-8")
        rep = chain.verify_daily_root("2026-09-10")
        assert not rep.ok and rep.reason == "root_chain_broken"

    def test_missing_root_reported(self, chain):
        rep = chain.verify_daily_root("2026-09-09")
        assert not rep.ok and rep.reason == "root_not_found"

    def test_verify_all_roots(self, chain):
        self._seed_day(chain, day="2026-09-09")
        chain.daily_merkle_root("2026-09-09")
        self._seed_day(chain, day="2026-09-10")
        chain.daily_merkle_root("2026-09-10")
        reps = chain.verify_daily_roots_all()
        assert len(reps) == 2 and all(r.ok for r in reps)

    def test_root_protection_flag_and_readonly(self, chain, paths):
        self._seed_day(chain)
        r = chain.daily_merkle_root("2026-09-09", protect=True)
        assert r.protected is True
        from agent.audit.migration import is_readonly
        assert is_readonly(paths["roots"]) is True
        os.chmod(paths["roots"], 0o644)   # 归还写权限，避免 tmp 清理失败

    def test_root_protection_can_be_disabled(self, chain, paths):
        self._seed_day(chain)
        r = chain.daily_merkle_root("2026-09-09", protect=False)
        assert r.protected is False
        from agent.audit.migration import is_readonly
        assert is_readonly(paths["roots"]) is False

    def test_roots_file_is_writable_before_each_append(self, chain, paths):
        """受保护文件在下次追加前自动恢复写权限（否则第二次追加会 PermissionError）"""
        self._seed_day(chain, day="2026-09-09")
        chain.daily_merkle_root("2026-09-09", protect=True)
        self._seed_day(chain, day="2026-09-10")
        chain.daily_merkle_root("2026-09-10", protect=True)
        assert len(chain.read_daily_roots()) == 2
        os.chmod(paths["roots"], 0o644)

    def test_signature_tamper_detected_through_replay(self, chain, paths):
        """直接换签名字段 → 外层根链（entry_hash）先行检出"""
        chain.append("act", "a", ts="2026-09-09T00:00:00.000000+00:00")
        chain.flush()
        root = chain.daily_merkle_root("2026-09-09")
        if root.signature_scheme != "ed25519":
            pytest.skip("环境无 ed25519 密钥，降级路径另有用例覆盖")
        os.chmod(paths["roots"], 0o644)
        rec = json.loads(pathlib.Path(paths["roots"]).read_text(
            encoding="utf-8").strip())
        rec["signature"] = "00" * 64
        pathlib.Path(paths["roots"]).write_text(
            json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        rep = chain.verify_daily_root("2026-09-09")
        assert not rep.ok
        assert rep.reason in ("signature_invalid", "root_chain_broken")

    def test_forged_root_record_with_recomputed_chain_caught_by_signature(self, chain,
                                                                         paths):
        """攻击者重算外层根链（无密钥哈希链）仍无法伪造 ed25519 签名"""
        chain.append("act", "a", ts="2026-09-09T00:00:00.000000+00:00")
        chain.flush()
        root = chain.daily_merkle_root("2026-09-09")
        if root.signature_scheme != "ed25519":
            pytest.skip("环境无 ed25519 密钥，降级路径另有用例覆盖")
        os.chmod(paths["roots"], 0o644)
        rec = json.loads(pathlib.Path(paths["roots"]).read_text(
            encoding="utf-8").strip())
        # 篡改「受签但重放不交叉核对」的字段（末条 self_hash），并重算外层哈希链
        rec["last_self_hash"] = "f" * 64
        forged = DailyRoot.from_dict(rec)
        forged.entry_hash = forged.compute_entry_hash(forged.prev_entry_hash)
        pathlib.Path(paths["roots"]).write_text(
            json.dumps(forged.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
        rep = chain.verify_daily_root("2026-09-09")
        assert rep.chains_ok is True           # 外层哈希链已被攻击者重算自洽
        assert rep.signature_ok is False       # 但签名无法伪造
        assert not rep.ok and rep.reason == "signature_invalid"

    def test_auto_seal_writes_root_for_past_day(self, paths):
        """auto_seal：后台自动为「已过完的日」封存 Merkle 根（无需人工触发）

        回归护栏（CI Shard6 崩溃根因）：只封**有记录**的日，且 writer 线程必须能退出——
        原实现按「最后一条 ts → 今天」逐日封存，2020 年记录会触发数千个空日封存，
        后台线程长时间不退出（close join 超时 → 线程泄漏 → 同进程其它用例不稳定）。
        """
        reset_audit_chains()
        c = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_key_path=paths["key"], auto_seal=True)
        try:
            c.append("act", "a", ts="2020-01-01T00:00:00.000000+00:00")
            deadline = time.time() + 5.0
            sealed = False
            while time.time() < deadline:
                if c.get_daily_root("2020-01-01") is not None:
                    sealed = True
                    break
                time.sleep(0.05)
            assert sealed, "auto_seal 未在 5s 内封存 2020-01-01 的根"
            assert c.verify_daily_root("2020-01-01").ok is True
            # 只封当天（不产生空日风暴）
            roots = c.read_daily_roots()
            assert [r.date for r in roots] == ["2020-01-01"]
            assert roots[0].leaf_count == 1
        finally:
            c.close(timeout=5.0)
            assert c._writer_thread is not None
            assert c._writer_thread.is_alive() is False, "writer 线程泄漏（未随 close 退出）"
            reset_audit_chains()
            if os.path.exists(paths["roots"]):
                os.chmod(paths["roots"], 0o644)

    def test_days_between_helper(self):
        assert AuditChain._days_between("2026-09-09", "2026-09-11") == \
            ["2026-09-09", "2026-09-10"]
        assert AuditChain._days_between("bad", "2026-09-11") == []
        assert AuditChain._days_between("2026-09-11", "2026-09-11") == []


# ════════════════════════════════════════════════════════════
#  9. 签名（ed25519 优先 / sha256 自签降级）
# ════════════════════════════════════════════════════════════


class TestSigning:
    def test_ed25519_scheme_when_key_available(self, chain):
        assert chain.signer.scheme in ("ed25519", "sha256-self")
        if chain.signer.scheme == "ed25519":
            assert chain.signer.degraded is False
            assert len(chain.signer.public_key_hex) == 64

    def test_signed_root_verifies(self, chain):
        chain.append("act", "a", ts="2026-09-09T00:00:00.000000+00:00")
        chain.flush()
        root = chain.daily_merkle_root("2026-09-09")
        assert root.signature
        rep = chain.verify_daily_root("2026-09-09")
        assert rep.signature_ok is True

    def test_wrong_signature_detected(self, chain):
        chain.append("act", "a", ts="2026-09-09T00:00:00.000000+00:00")
        chain.flush()
        root = chain.daily_merkle_root("2026-09-09")
        if root.signature_scheme != "ed25519":
            pytest.skip("环境无 ed25519 密钥，降级路径由下一用例覆盖")
        root.signature = sha256_hex("forged")
        assert RootsSigner.verify(root.signed_message(), root.signature,
                                  scheme=root.signature_scheme,
                                  public_key_hex=root.signer_public_key) is False

    def test_degraded_scheme_when_signing_disabled(self, paths):
        reset_audit_chains()
        c = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_key_path=paths["key"], signing_enabled=False,
                       auto_seal=False)
        c.append("act", "a", ts="2026-09-09T00:00:00.000000+00:00")
        c.flush()
        root = c.daily_merkle_root("2026-09-09")
        assert root.signature_scheme == "sha256-self"
        assert root.degraded is True and root.degraded_reason
        rep = c.verify_daily_root("2026-09-09")
        assert rep.ok and rep.signature_ok is True   # 自签占位一致性重算通过
        c.close()
        reset_audit_chains()

    def test_degraded_path_explicitly_recorded(self, paths):
        reset_audit_chains()
        c = AuditChain(paths["db"], roots_path=paths["roots"],
                       signing_key_path=str(pathlib.Path(paths["key"]).parent / "nope" / "k.pem"),
                       signing_enabled=True, auto_seal=False)
        c.append("act", "a")
        c.flush()
        root = c.daily_merkle_root()
        assert root.signature_scheme in ("ed25519", "sha256-self")
        if root.signature_scheme == "sha256-self":
            assert "降级" in root.degraded_reason
        c.close()
        reset_audit_chains()

    def test_sha256_self_verify_recomputes(self):
        msg = "m"
        sig = sha256_hex("sha256-self|" + msg)
        assert RootsSigner.verify(msg, sig, scheme="sha256-self") is True
        assert RootsSigner.verify(msg, "bad", scheme="sha256-self") is False

    def test_unknown_scheme_rejected(self):
        assert RootsSigner.verify("m", "s", scheme="rsa-fake") is False

    def test_ed25519_without_public_key_rejected(self):
        assert RootsSigner.verify("m", "00" * 64, scheme="ed25519",
                                  public_key_hex="") is False


# ════════════════════════════════════════════════════════════
#  10. append-only 不变量（源码级）
# ════════════════════════════════════════════════════════════


class TestAppendOnlyInvariants:
    @staticmethod
    def _source() -> str:
        return pathlib.Path(chain_mod.__file__).read_text(encoding="utf-8")

    def test_no_update_statement_in_source(self):
        assert "UPDATE audit_chain" not in self._source()

    def test_only_delete_is_in_clear(self):
        src = self._source()
        assert src.count("DELETE FROM audit_chain") == 1
        idx = src.index("DELETE FROM audit_chain")
        assert "def clear" in src[max(0, idx - 400):idx]

    def test_writer_insert_only(self):
        src = self._source()
        start = src.index("def _write_to_db")
        body = src[start:start + 1600]
        assert "INSERT INTO audit_chain" in body
        assert "UPDATE" not in body

    def test_seq_has_unique_constraint(self, chain):
        with chain._connect() as conn:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='audit_chain'").fetchone()[0]
        assert "seq INTEGER NOT NULL UNIQUE" in ddl

    def test_wal_journal_mode_configured(self, chain):
        with chain._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_no_persistent_file_handle_when_idle(self, chain):
        """文件库用完即关：空闲时不占住台账文件（否则 Windows 上无法删除/替换）"""
        chain.append("act", "a")
        chain.flush()
        chain.close()
        os.remove(chain.db_path)          # 能删掉 = 无残留句柄


# ════════════════════════════════════════════════════════════
#  11. 降级与鲁棒性
# ════════════════════════════════════════════════════════════


class TestDegradation:
    def test_db_init_failure_degrades_to_ring_buffer(self, tmp_path):
        """SQLite 不可用 → ring buffer 兜底（审计不丢、不抛）"""
        reset_audit_chains()
        bad = str(tmp_path / "as_dir")
        os.makedirs(bad, exist_ok=True)   # 目录占位 → 打不开 DB
        c = AuditChain(bad, roots_path=str(tmp_path / "r.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
        assert c.degraded is True
        assert c._db_available is False
        e = c.append("act", "a")
        assert e.seq == 1
        c.flush(timeout=2.0)
        got = c.entries()
        assert [x.seq for x in got] == [1]           # ring buffer 内可见
        assert c.verify_chain().ok is True
        c.close()
        reset_audit_chains()

    def test_failed_write_keeps_records_visible(self, chain):
        chain.append("act", "a")
        chain.flush()
        # 模拟写失败：直接调用批量写并把连接指向无效路径
        entry = chain.append("act2", "a")
        chain._db_available = False
        chain.flush(timeout=1.0)
        assert any(e.seq == entry.seq for e in chain.entries())

    def test_verify_works_on_degraded_chain(self, chain):
        """降级（DB 不可用）台账仍可验签：记录在 ring buffer 内自洽成链"""
        chain._db_available = False
        chain.append("act", "a")
        chain.flush(timeout=2.0)
        v = run_verify_chain(chain.entries())
        assert v.ok and v.checked == 1
        assert chain.verify_chain().ok is True

    def test_normalize_day_accepts_multiple_types(self):
        from datetime import date, datetime, timezone
        assert chain_mod._normalize_day("2026-09-09T05:00:00+00:00") == "2026-09-09"
        assert chain_mod._normalize_day(date(2026, 9, 9)) == "2026-09-09"
        assert chain_mod._normalize_day(
            datetime(2026, 9, 9, tzinfo=timezone.utc)) == "2026-09-09"

    def test_normalize_ts_variants(self):
        assert chain_mod.normalize_ts("2026-09-09").startswith("2026-09-09")
        assert "T" in chain_mod.normalize_ts(1_700_000_000)
        assert chain_mod.normalize_ts(None).endswith("+00:00")

    def test_default_db_path_is_data_audit(self):
        assert DEFAULT_DB_PATH.replace("\\", "/").endswith("data/audit/audit_chain.db")

    def test_reset_audit_chains_closes_and_clears(self, paths):
        c = get_audit_chain(paths["db"], roots_path=paths["roots"],
                            signing_key_path=paths["key"])
        reset_audit_chains()
        assert c.closed is True
        assert chain_mod.active_writers() == {}


# ════════════════════════════════════════════════════════════
#  12. 性能（§11.2 量级：单条 append <5ms）
# ════════════════════════════════════════════════════════════


class TestPerformance:
    def test_append_mean_latency_under_5ms(self, chain):
        n = 50
        lat = []
        for i in range(n):
            t0 = time.perf_counter()
            chain.append("perf.probe", "bench", f"s{i}", {"i": i})
            lat.append((time.perf_counter() - t0) * 1000.0)
        mean = sum(lat) / len(lat)
        assert mean < 5.0, f"单条 append 均值 {mean:.3f}ms 超出 5ms 预算"

    def test_batch_persist_after_burst(self, chain):
        for i in range(40):
            chain.append(f"burst{i}", "b")
        assert chain.flush(timeout=10.0)
        assert chain.count() == 40
        assert chain.verify_chain().ok
