"""L1-b 回归：每日根"叶子集合"口径必须唯一（该 UTC 日 ∩ seq ≤ 封印点）

【被测缺陷】
    ``daily_merkle_root()``（生成侧，决定被签名的 ``root_hash``/``leaf_count``）按
    **日**取叶；``verify_daily_root()``（校验侧）旧实现按 **seq 区间**
    ``[first_seq, last_seq]`` 取叶。两者只在"seq 顺序 == ts 顺序"时恰好一致；
    一旦某日的 seq 区间里混有**其它日**的记录（生产库 2026-09-14 的 915..2782 里有
    1786 条测试污染记录），校验侧就重算出**另一个叶子集合** ⇒ 该日**恒定 FAIL**。

【统一后的口径（两个约束缺一不可）】
    叶子 = ``entries(day=day, end_seq=recorded.last_seq)``：
    - **按日**过滤：排除封印区间内其它日的记录（修掉 09-14 恒 FAIL）；
    - **封印点上界** ``seq ≤ last_seq``：封印后新入链记录（seq 更大）不进集合
      （保住 S2-02 的"封印后追加不误报"回归保护）。

【为什么以"按日"为准（证据，不是偏好）】6 个已签名生产日根的 ``root_hash`` 与
    ``leaf_count`` **全部**等于按日重算的值（含 09-14：82 叶而非 1868 叶），
    而这两个字段都在签名消息内 ⇒ 签名的对象就是"该日叶子集合"。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

from agent.audit.chain import AuditChain, merkle_root, reset_audit_chains

pytestmark = [pytest.mark.unit, pytest.mark.p3]

_DAY_A = "2026-03-01"
_DAY_B = "2026-03-02"


@pytest.fixture(autouse=True)
def _cleanup():
    reset_audit_chains()
    yield
    reset_audit_chains()


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """临时链：不签名、不自动封存、不置只读（只读文件会让用例改写日根失败）"""
    monkeypatch.setenv("AUDIT_SIGNING_KEY", str(tmp_path / "k.pem"))
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "daily_roots.jsonl"),
                   signing_enabled=False, auto_seal=False,
                   daily_root_protect=False)
    yield c
    try:
        c.close(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


def _seed_interleaved(c: AuditChain) -> None:
    """A/B 两日**交错**入链：A 的 seq 区间 [1,5] 里夹着 2 条 B 日记录

    seq 顺序：1(A) 2(B) 3(A) 4(B) 5(A)  ⇒ A: first_seq=1,last_seq=5,leaf_count=3
    区间叶子数 5 ≠ 日叶子数 3 —— 这正是旧校验口径误判 FAIL 的数据形态。
    """
    for i, day in enumerate([_DAY_A, _DAY_B, _DAY_A, _DAY_B, _DAY_A]):
        c.append("interleaved.write", actor=f"a{i}",
                 ts=f"{day}T0{i}:00:00.000000+00:00")
    c.flush()


def _tamper_db(db_path: str, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _recorded(c: AuditChain, day: str):
    rec = [r for r in (json.loads(x) for x in
                       pathlib.Path(c.roots_path).read_text(
                           encoding="utf-8").splitlines() if x.strip())
           if r.get("date") == day]
    assert rec, f"日根未写入: {day}"
    return rec[-1]


def test_cross_convention_replay_verifies_when_days_interleave(chain):
    """**跨口径一致性测试**：``daily_merkle_root()`` 写下的根必须能被重放校验通过

    （缺陷形态下必定失败：重放取的是 5 条区间叶子，而根是 3 条日叶子算出来的）
    """
    _seed_interleaved(chain)
    root_a = chain.daily_merkle_root(_DAY_A)
    root_b = chain.daily_merkle_root(_DAY_B)

    assert (root_a.first_seq, root_a.last_seq, root_a.leaf_count) == (1, 5, 3)
    assert (root_b.first_seq, root_b.last_seq, root_b.leaf_count) == (2, 4, 2)

    rep_a = chain.verify_daily_root(_DAY_A)
    rep_b = chain.verify_daily_root(_DAY_B)
    assert rep_a.ok is True, rep_a.summary()
    assert rep_b.ok is True, rep_b.summary()
    assert (rep_a.entries_verified, rep_b.entries_verified) == (3, 2)
    # 诊断字段把"两种口径在这批数据上确实不同"钉下来（区间 5 叶、其中 2 条是别的日）
    assert rep_a.interval_leaf_count == 5
    assert rep_a.interval_foreign_count == 2


def test_recorded_root_equals_day_leaves_not_interval_leaves(chain):
    """判定依据：签名的 ``root_hash`` 等于**按日**重算值，**不等于**区间重算值

    这条用例把"哪一种口径才是签名语义"从"偏好"变成"可执行证据"：
    ``leaf_count``/``root_hash`` 都在签名消息内，而它们与按日口径一致。
    """
    _seed_interleaved(chain)
    root_a = chain.daily_merkle_root(_DAY_A)
    rec = _recorded(chain, _DAY_A)

    day_leaves = [e.self_hash for e in chain.entries(day=_DAY_A,
                                                     end_seq=rec["last_seq"])]
    interval_leaves = [e.self_hash for e in chain.entries(start_seq=rec["first_seq"],
                                                          end_seq=rec["last_seq"])]
    assert len(day_leaves) == 3 and len(interval_leaves) == 5
    assert root_a.root_hash == merkle_root(day_leaves)
    assert root_a.root_hash != merkle_root(interval_leaves)
    assert rec["leaf_count"] == len(day_leaves)   # 签名内的叶数与按日口径一致


def test_seal_point_upper_bound_keeps_late_same_day_append_clean(chain):
    """**保住 S2-02 的回归保护**：封印后同一天又入链 ⇒ 不误报（seq > last_seq）

    这是"封印 = 封印点之前的该日记录集合"里的**上界**约束；没有它，
    "封当日/事后回填"就会误报为篡改（S2-02 交付报告记录的原始 bug）。
    """
    _seed_interleaved(chain)
    root_a = chain.daily_merkle_root(_DAY_A)
    chain.append("late.same.day", actor="late",
                 ts=f"{_DAY_A}T23:59:59.000000+00:00")
    chain.flush()
    rep = chain.verify_daily_root(_DAY_A)
    assert rep.ok is True, rep.summary()
    assert rep.entries_verified == 3, "封印后新记录不得进入该根的叶子集合"
    assert root_a.last_seq == 5 and chain.count() == 6


def test_replay_detects_day_record_tamper(chain):
    """**D7 守卫**：该日记录被改（payload/字段）仍必须检出（口径统一不得放宽）"""
    _seed_interleaved(chain)
    chain.daily_merkle_root(_DAY_A)
    _tamper_db(chain.db_path, "UPDATE audit_chain SET actor='mallory' WHERE seq=3")
    rep = chain.verify_daily_root(_DAY_A)
    assert not rep.ok and rep.reason in ("root_hash_mismatch", "entry_hash_mismatch")


def test_replay_detects_day_record_deletion(chain):
    """**D7 守卫**：该日记录被删 → 叶子数/根哈希双检必报"""
    _seed_interleaved(chain)
    chain.daily_merkle_root(_DAY_A)
    _tamper_db(chain.db_path, "DELETE FROM audit_chain WHERE seq=3")
    rep = chain.verify_daily_root(_DAY_A)
    assert not rep.ok and rep.reason in ("root_hash_mismatch", "leaf_count_mismatch")


def test_replay_detects_seal_metadata_tamper(chain):
    """**新增检出**：封印元数据（last_seq）被改也不得放过

    旧口径直接**拿它当查询边界**（改了只会换个叶子集合、可能"碰巧"通过），
    新口径把它变成与实际记录的一致性断言。
    """
    _seed_interleaved(chain)
    chain.daily_merkle_root(_DAY_A)
    rec = _recorded(chain, _DAY_A)
    rec["last_seq"] = 7                      # 声称封印到 seq 7（实际该日止于 5）
    pathlib.Path(chain.roots_path).write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    rep = chain.verify_daily_root(_DAY_A)
    assert not rep.ok, "封印元数据被改却通过（元数据未做一致性核对）"


def test_empty_day_root_rejects_late_backfill(chain):
    """空日根（"该日无新增"是**绝对**断言）：事后回填该日记录必须被检出"""
    _seed_interleaved(chain)                 # 先有 A/B 两日数据
    chain.daily_merkle_root("2026-03-09")    # 空日根（leaf_count=0）
    assert chain.get_daily_root("2026-03-09").leaf_count == 0
    chain.append("backfill.write", actor="bf",
                 ts="2026-03-09T10:00:00.000000+00:00")
    chain.flush()
    rep = chain.verify_daily_root("2026-03-09")
    assert not rep.ok, "空日根被事后回填却仍报通过（该日无新增的断言已失效）"
    assert rep.reason in ("root_hash_mismatch", "leaf_count_mismatch")
    assert rep.entries_verified == 1, "空日根应按该日**全部**记录核对（不给前缀上界）"
