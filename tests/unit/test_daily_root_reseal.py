"""D5 · 每日 Merkle 根重封与覆盖补齐（`agent/audit/chain.py` 封印解析 + 重封工具）

【被测缺陷（D3 实测，本卡复核）】
    `daily_roots.jsonl` 是**只追加**的封印日志，同一天可以有多条记录；而
    `get_daily_root()` 旧实现按日期取**第一条** ⇒ 追加式重封对 `verify_daily_root`
    **完全无效**：新根自洽，验签读到的仍是旧根（生产 2026-09-21 重现）。

【本文件的断言口径】
    全部用例只吃 `tmp_path` 假库（不读、不写生产 `data/audit/`）；
    关于"既有有效日根的验签行为不变"用**同日重复记录**（生产 2026-09-14 的形态）
    等价构造并在假库上锁定；真实生产数据的对照见报告 D5.md。
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agent.audit import chain as chain_mod
from agent.audit.chain import (AuditChain, RootsSigner, merkle_root,
                               reset_audit_chains)

pytestmark = [pytest.mark.unit, pytest.mark.p3]

_SCRIPT_PATH = (pathlib.Path(__file__).resolve().parents[2]
                / "scripts" / "audit_reseal_daily_root.py")


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def shared_key(tmp_path_factory):
    """会话级共享 ed25519 私钥（避免每个用例一次 OpenSSL keygen）

    **必须在这里就把密钥落盘**：`RootsSigner` 是**惰性**生成密钥的（只有真正签名时
    才 keygen），而"私钥缺失"会导致脚本拒绝 --apply（这正是它的前置守卫）。
    若把 keygen 留给"第一个用到签名的用例"，用例结果就会依赖**执行顺序**
    （实测：随机顺序下 `test_missing_day_dry_run_then_apply_same_plan` 偶发失败）。
    这里显式初始化一次，等价于生产部署"私钥已存在"的稳态。
    """
    path = str(tmp_path_factory.mktemp("d5_keys") / "audit_signing_key.pem")
    signer = RootsSigner(path, enabled=True)
    signer.sign("d5-key-init")          # 触发一次性 keygen
    assert os.path.exists(path)
    return path


@pytest.fixture(autouse=True)
def _cleanup():
    reset_audit_chains()
    yield
    reset_audit_chains()


@pytest.fixture
def script():
    """按路径加载被测脚本（`scripts/` 不是包，故用 importlib 显式加载）"""
    spec = importlib.util.spec_from_file_location("d5_reseal_script",
                                                  str(_SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Fake(object):
    """tmp_path 假库：一个 writer 台账 + 一份日根文件"""

    def __init__(self, tmp_path, key):
        self.db = str(tmp_path / "audit_chain.db")
        self.roots = str(tmp_path / "daily_roots.jsonl")
        self.key = key
        reset_audit_chains()
        self.chain = AuditChain(self.db, roots_path=self.roots,
                                signing_key_path=self.key, auto_seal=False,
                                daily_root_protect=False)

    # ── 运行脚本 ──
    def argv(self, *extra):
        return ["--db", self.db, "--roots", self.roots, "--key-path", self.key,
                *extra]

    def run(self, script_mod, *extra):
        return script_mod.main(self.argv(*extra))

    # ── 读盘助手 ──
    def roots_bytes(self):
        p = pathlib.Path(self.roots)
        return p.read_bytes() if p.exists() else b""

    def db_rows(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT seq, ts, actor, action, subject, payload_hash, prev_hash, "
                "self_hash, source, trace_id, workspace_id, schema_version, payload "
                "FROM audit_chain ORDER BY seq").fetchall()
            return [tuple(r) for r in rows]
        finally:
            conn.close()

    def close(self):
        try:
            self.chain.close(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def fake(tmp_path, shared_key):
    f = Fake(tmp_path, shared_key)
    yield f
    f.close()
    # 脚本默认按生产口径把日根文件置只读；归还写权限，避免 tmp 清理告警
    try:
        if os.path.exists(f.roots):
            os.chmod(f.roots, 0o644)
    except Exception:  # noqa: BLE001
        pass


def _seed(chain, day, n, start_hour=0):
    """造 n 条某 UTC 日的记录（seq 递增）"""
    for i in range(n):
        chain.append("d5.seed", "alice", "s%d" % i,
                     ts="%sT%02d:00:00.000000+00:00" % (day, start_hour + i))
    chain.flush()


def _mismatching_root(chain, day, keep):
    """追加一条"封印时只看到前 keep 条"的根 —— 2026-09-21 形态的等价构造

    生产 09-21：记录 leaf_count=235 / last_seq=20349，而该日 seq <= 20349 实际有 245 条。
    这里同样让"记录里的叶子集合"小于"封印点上界内的实际叶子集合"，
    于是重放必报 `root_hash_mismatch`（而不是叶子数或元数据类原因）。
    """
    obj = chain.daily_merkle_root(day, write=False, sign=True)
    full = chain.entries(day=day)
    obj.root_hash = merkle_root([e.self_hash for e in full[:keep]])
    obj.leaf_count = keep
    return chain._append_daily_root(obj, protect=False)


# ════════════════════════════════════════════════════════════
#  1. 缺根日补封
# ════════════════════════════════════════════════════════════


def test_missing_day_is_sealed_then_verify_ok(fake, script):
    """有记录但没日根的日 → 补封后 verify_daily_root 通过"""
    _seed(fake.chain, "2026-03-01", 3)
    _seed(fake.chain, "2026-03-02", 4, start_hour=10)
    fake.chain.daily_merkle_root("2026-03-01")

    assert fake.chain.get_daily_root("2026-03-02") is None
    assert fake.chain.verify_daily_root("2026-03-02").reason == "root_not_found"

    assert fake.run(script, "--all-missing", "--apply") == 0

    v = fake.chain.verify_daily_root("2026-03-02")
    assert v.ok, v.summary()
    assert v.entries_verified == 4
    assert (v.seal_index, v.seal_total) == (1, 1)
    assert v.signature_ok is True
    assert v.chains_ok is True


def test_missing_day_dry_run_then_apply_same_plan(fake, script):
    """dry-run 只报告；--apply 写下的根与 dry-run 预演的根**逐字相同**"""
    _seed(fake.chain, "2026-03-01", 4)
    planned = fake.chain.daily_merkle_root("2026-03-01", write=False, sign=False)

    assert fake.run(script, "--all-missing") == 0
    assert fake.chain.get_daily_root("2026-03-01") is None      # dry-run 未写
    assert fake.roots_bytes() == b""

    assert fake.run(script, "--all-missing", "--apply") == 0
    written = fake.chain.get_daily_root("2026-03-01")
    assert written.root_hash == planned.root_hash
    assert written.leaf_count == planned.leaf_count
    assert (written.first_seq, written.last_seq) == (planned.first_seq,
                                                     planned.last_seq)


# ════════════════════════════════════════════════════════════
#  2. 「根与实际不符」的日（2026-09-21 形态）
# ════════════════════════════════════════════════════════════


def test_reseal_fixes_root_disagreeing_with_actual(fake, script):
    """记录与实际不符 → 重封后通过；且被取代的历史记录**原样保留**"""
    day = "2026-03-05"
    _seed(fake.chain, day, 5)
    _mismatching_root(fake.chain, day, keep=3)

    before = fake.chain.verify_daily_root(day)
    assert before.ok is False and before.reason == "root_hash_mismatch"
    roots_before = fake.roots_bytes()
    rows_before = fake.db_rows()

    assert fake.run(script, "--date", day, "--apply") == 0

    after = fake.chain.verify_daily_root(day)
    assert after.ok, after.summary()
    assert after.entries_verified == 5
    assert (after.seal_index, after.seal_total) == (2, 2)

    # 只追加：旧字节是新字节的前缀；台账一行未动
    assert fake.roots_bytes().startswith(roots_before)
    assert fake.db_rows() == rows_before

    # 历史记录没被删改：显式取回后**仍如实报不符**
    hist = fake.chain.get_daily_root(day, seal=1)
    assert hist is not None and hist.leaf_count == 3
    assert fake.chain.verify_daily_root(day, seal=1).reason == "root_hash_mismatch"


def test_reseal_resealed_record_is_exactly_the_recomputed_root(fake):
    """重封写下的 root_hash 等于按「该日 ∩ seq <= last_seq」重算的值（自洽）"""
    day = "2026-03-06"
    _seed(fake.chain, day, 6)
    _mismatching_root(fake.chain, day, keep=2)

    res = fake.chain.reseal_daily_root(day)
    assert res.applied is True
    assert res.previous is not None and res.previous.leaf_count == 2
    assert res.current is not None
    leaves = [e.self_hash for e in fake.chain.entries(day=day,
                                                      end_seq=res.current.last_seq)]
    assert res.current.root_hash == merkle_root(leaves)
    assert res.current.leaf_count == len(leaves) == 6
    assert res.verification is not None and res.verification.ok is True


# ════════════════════════════════════════════════════════════
#  3. 幂等
# ════════════════════════════════════════════════════════════


def test_already_correct_root_reseal_is_idempotent(fake, script):
    """已正确的日根重封是幂等的：不产生重复记录、不改一个字节"""
    day = "2026-03-01"
    _seed(fake.chain, day, 4)

    assert fake.run(script, "--date", day, "--apply") == 0
    assert len(fake.chain.daily_root_records(day)) == 1
    roots_after_first = fake.roots_bytes()

    for _ in range(2):
        assert fake.run(script, "--date", day, "--apply") == 0
        assert len(fake.chain.daily_root_records(day)) == 1
        assert fake.roots_bytes() == roots_after_first

    res = fake.chain.reseal_daily_root(day)
    assert res.applied is False and res.reason == "already_ok"
    assert fake.roots_bytes() == roots_after_first


def test_force_reseal_appends_even_when_current_is_ok(fake):
    """force=True 是显式的"再封一条"，与默认幂等语义区分开"""
    day = "2026-03-01"
    _seed(fake.chain, day, 3)
    fake.chain.daily_merkle_root(day)

    res = fake.chain.reseal_daily_root(day, force=True)
    assert res.applied is True
    assert len(fake.chain.daily_root_records(day)) == 2
    assert fake.chain.verify_daily_root(day).ok is True


# ════════════════════════════════════════════════════════════
#  4. 既有有效日根的验签行为不变（同日多条记录）
# ════════════════════════════════════════════════════════════


def test_existing_valid_roots_verification_unchanged(fake):
    """**回归锁定**：取用语义从"第一条"改为"最后一条"后，既有有效根的验签结果不变

    构造与生产 2026-09-14 同形：某日有两条记录，且两条的
    root_hash / leaf_count / first_seq / last_seq / 首尾 self_hash 完全相同
    （只有 created_at / prev_entry_hash / entry_hash 不同）。
    """
    days = ["2026-03-01", "2026-03-02", "2026-03-03"]
    for i, d in enumerate(days):
        _seed(fake.chain, d, 3 + i, start_hour=i * 5)
    for d in days:
        fake.chain.daily_merkle_root(d)
    dup = fake.chain.daily_merkle_root("2026-03-02", force=True)   # 同日第二条

    assert len(fake.chain.daily_root_records("2026-03-02")) == 2
    # 两条记录参与验签的字段逐字相同（同生产 09-14）
    first, last = [r for _, r in fake.chain.daily_root_records("2026-03-02")]
    assert first.root_hash == last.root_hash == dup.root_hash
    assert first.leaf_count == last.leaf_count
    assert (first.first_seq, first.last_seq) == (last.first_seq, last.last_seq)
    assert first.first_self_hash == last.first_self_hash
    assert first.last_self_hash == last.last_self_hash

    for d in days:
        v = fake.chain.verify_daily_root(d)
        assert v.ok is True, v.summary()
        assert v.seal_total == (2 if d == "2026-03-02" else 1)
        # 单条记录的日子：默认取用 == 显式 seal=1（语义变更对既有根零影响）
        if d != "2026-03-02":
            v1 = fake.chain.verify_daily_root(d, seal=1)
            assert (v1.ok, v1.recorded_root, v1.entries_verified) == (
                v.ok, v.recorded_root, v.entries_verified)
            assert v.seal_index == 1

    # 两条内容相同的记录 ⇒ 取第一条 / 取最后一条的验签结果一致
    v1 = fake.chain.verify_daily_root("2026-03-02", seal=1)
    v2 = fake.chain.verify_daily_root("2026-03-02", seal=2)
    assert v1.ok is True and v2.ok is True
    assert v1.recorded_root == v2.recorded_root == dup.root_hash


def test_seal_selector_out_of_range_and_unknown_day(fake):
    """seal 越界 / 未知日期 → None（不抛异常，保持 Optional 契约）"""
    _seed(fake.chain, "2026-03-01", 2)
    fake.chain.daily_merkle_root("2026-03-01")

    assert fake.chain.get_daily_root("2026-03-09") is None
    assert fake.chain.get_daily_root("2026-03-01", seal=2) is None
    assert fake.chain.get_daily_root("2026-03-01", seal=0) is None
    assert fake.chain.get_daily_root("2026-03-01", seal=1) is not None
    v = fake.chain.verify_daily_root("2026-03-01", seal=2)
    assert v.ok is False and v.reason == "root_not_found"
    assert v.seal_total == 1


# ════════════════════════════════════════════════════════════
#  5. dry-run 不写任何东西
# ════════════════════════════════════════════════════════════


def test_dry_run_writes_nothing(fake, script):
    """默认模式（无 --apply）不写日根、不动台账"""
    _seed(fake.chain, "2026-03-01", 3)
    fake.chain.daily_merkle_root("2026-03-01")
    _seed(fake.chain, "2026-03-02", 4, start_hour=10)      # 缺根日
    fake.chain.flush()

    roots_before = fake.roots_bytes()
    rows_before = fake.db_rows()

    assert fake.run(script, "--all-missing") == 0
    assert fake.run(script, "--date", "2026-03-02") == 0

    assert fake.roots_bytes() == roots_before
    assert fake.db_rows() == rows_before
    assert fake.chain.get_daily_root("2026-03-02") is None


def test_dry_run_on_empty_roots_file_does_not_create_it(fake, script, tmp_path):
    """日根文件不存在时，dry-run 也不得创建它"""
    _seed(fake.chain, "2026-03-01", 2)
    assert not pathlib.Path(fake.roots).exists()

    assert fake.run(script, "--all-missing") == 0
    assert not pathlib.Path(fake.roots).exists()


# ════════════════════════════════════════════════════════════
#  6. --apply 只追加日根行、台账零写入
# ════════════════════════════════════════════════════════════


def test_apply_only_appends_roots_and_leaves_chain_rows_untouched(fake, script):
    """台账 13 列逐行完全未变；日根文件只多出行（旧字节是新字节前缀）"""
    _seed(fake.chain, "2026-03-01", 3)
    fake.chain.daily_merkle_root("2026-03-01")
    _seed(fake.chain, "2026-03-02", 4, start_hour=10)
    _mismatching_root(fake.chain, "2026-03-02", keep=1)
    fake.chain.flush()

    roots_before = fake.roots_bytes()
    rows_before = fake.db_rows()
    db_bytes_before = pathlib.Path(fake.db).read_bytes()

    # 【为什么用 --date 而不是 --all-missing】--all-missing 只补"没有日根"的日，
    # 刻意不批量重封"已有根但验签不过"的日（见脚本 docstring）；不符日必须点名。
    assert fake.run(script, "--date", "2026-03-02", "--apply") == 0

    assert fake.db_rows() == rows_before
    assert pathlib.Path(fake.db).read_bytes() == db_bytes_before
    roots_after = fake.roots_bytes()
    assert roots_after.startswith(roots_before)
    assert len(roots_after) > len(roots_before)
    assert fake.chain.verify_daily_root("2026-03-02").ok is True


def test_apply_never_calls_chain_append(fake, script, monkeypatch):
    """把 `AuditChain.append` 换成抛异常的桩：重封流程仍必须跑通"""
    _seed(fake.chain, "2026-03-01", 3)
    _seed(fake.chain, "2026-03-02", 2, start_hour=10)
    fake.chain.daily_merkle_root("2026-03-01")
    fake.chain.flush()
    fake.close()

    def _boom(*_args, **_kwargs):
        raise AssertionError("重封流程不得调用 AuditChain.append（会写台账）")

    monkeypatch.setattr(chain_mod.AuditChain, "append", _boom)
    assert fake.run(script, "--all-missing", "--apply") == 0
    assert fake.chain.verify_daily_root("2026-03-02").ok is True


def test_destructive_sql_self_check_is_not_vacuous(script, tmp_path):
    """负向探针：自检必须真的会红（否则它只是恒绿的空断言）"""
    script._self_check_no_destructive_sql()          # 自检自身：通过
    bad = tmp_path / "injected_script.py"
    bad.write_text("SQL = 'delete from audit_chain where seq = 1'"
                   + chr(10), encoding="utf-8")
    with pytest.raises(RuntimeError):
        script._self_check_no_destructive_sql(str(bad))


def test_tamper_class_failure_is_refused_by_default(fake, script):
    """篡改类失败（记录两级哈希重算不一致）默认**拒绝**重封：重封会把发现抹掉"""
    day = "2026-03-07"
    _seed(fake.chain, day, 3)
    fake.chain.daily_merkle_root(day)

    conn = sqlite3.connect(fake.db)
    conn.execute("UPDATE audit_chain SET actor = 'mallory' WHERE seq = 2")
    conn.commit()
    conn.close()

    v = fake.chain.verify_daily_root(day)
    assert v.ok is False and v.reason in ("entry_hash_mismatch",
                                          "root_chain_broken",
                                          "signature_invalid")
    roots_before = fake.roots_bytes()

    assert fake.run(script, "--date", day, "--apply") == 2
    assert fake.roots_bytes() == roots_before          # 一个字节都没写


# ════════════════════════════════════════════════════════════
#  7. 日期与前置条件的安全阀
# ════════════════════════════════════════════════════════════


def test_future_day_skipped_unless_explicitly_allowed(fake, script):
    """未来日期（生产实测 2027-10-19/20 那类）默认跳过，--include-future 才处理"""
    future = (datetime.now(timezone.utc).date()
              + timedelta(days=400)).isoformat()
    _seed(fake.chain, future, 2)

    assert fake.run(script, "--all-missing", "--apply") == 0
    assert fake.chain.get_daily_root(future) is None

    assert fake.run(script, "--all-missing", "--include-future", "--apply") == 0
    assert fake.chain.verify_daily_root(future).ok is True


def test_exclude_date_wins(fake, script):
    _seed(fake.chain, "2026-03-01", 2)
    assert fake.run(script, "--all-missing", "--exclude", "2026-03-01",
                    "--apply") == 0
    assert fake.chain.get_daily_root("2026-03-01") is None


def test_day_without_entries_is_rejected(fake, script):
    """台账里没有任何记录的日期：拒绝服务，绝不凭空封出空日根"""
    _seed(fake.chain, "2026-03-01", 2)
    assert fake.run(script, "--date", "2026-05-05", "--apply") == 2
    assert fake.chain.read_daily_roots() == []
    assert fake.roots_bytes() == b""

    res = fake.chain.reseal_daily_root("2026-05-05")
    assert res.applied is False and res.reason == "day_not_found"
    assert fake.roots_bytes() == b""


def test_apply_requires_target_selector(fake, script):
    """--apply 必须带 --date / --all-missing（不给"猜目标"留口子）"""
    _seed(fake.chain, "2026-03-01", 2)
    assert fake.run(script, "--apply") == 2
    assert fake.roots_bytes() == b""


def test_apply_refuses_when_signing_key_missing(fake, script, tmp_path):
    """私钥缺失时拒绝 --apply，并且**绝不生成**私钥文件"""
    _seed(fake.chain, "2026-03-01", 3)
    missing = tmp_path / "no_such_key.pem"

    rc = script.main(["--db", fake.db, "--roots", fake.roots,
                      "--key-path", str(missing), "--all-missing", "--apply"])
    assert rc == 2
    assert not missing.exists()
    assert fake.roots_bytes() == b""

    rc = script.main(["--db", fake.db, "--roots", fake.roots,
                      "--key-path", str(missing), "--all-missing"])
    assert rc == 0                     # dry-run 不需要私钥
    assert not missing.exists()
