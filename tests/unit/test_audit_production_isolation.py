"""生产审计面隔离的**会话级守卫**回归（TASK-A / 遗留 L2）

【本文件为什么单独存在】
    `tests/unit/test_audit_logger_comprehensive.py` 自己带一个 autouse 夹具，把模块级单例
    重绑到该用例的 `tmp_path` ⇒ 在那个文件里**无法**检出「`tests/conftest.py` 的会话级
    守卫被移除」这件事（局部夹具会把它掩盖掉）。本文件**不带**局部夹具，只依赖会话级守卫，
    因此它的通过等价于"守卫在位"。

【为什么本文件刻意只做只读断言】
    若守卫被移除，本文件的断言会失败 —— 此时若它还带写操作，就会**先污染生产链再报红**。
    故写型断言一律留在有 `tmp_path` 兜底的文件里；本文件只读路径绑定与生产库。

【AUDIT-ROOT-REPAIR（2026-09-26）追加的三条 —— 每日 Merkle 根路径的**构造级**隔离】
    根因：`agent/audit/chain.py` 的构造器原先只认显式 `roots_path=` 形参、**不读**
    `AUDIT_ROOTS_PATH`（门面 `facade.py` 读），于是"直接用 `AuditChain(tmp_db)`"的
    测试即使被会话级守卫隔离了 env，也会把**测试小链的日根**追加进生产
    `data/audit/daily_roots.jsonl`（实测 2026-09-25 一天 3 条 leaf_count=2/4 的竞争记录）。
    本文件新增的三条与上面两条同一主题（"测得到生产面"），因此放在一起；它们**不破坏**
    本文件"不写生产"的前提：
      · `test_audit_roots_env_is_session_isolated`：只读 env 与路径；
      · `test_ctor_honours_env_roots_path`：`monkeypatch.setenv` 到 `tmp_path` 后再构造，
        与生产文件无关；
      · `test_auto_seal_of_test_chain_never_touches_production_roots`：**先断言**
        `chain._roots_path` 不在生产审计目录，**再**写 —— 一旦 chain.py 那一行被改回
        "不读 env"，用例在写入之前就红，不会自己变成污染源（写入前的生产文件
        size/mtime_ns 差集仍作为事后证据断言）。

【根因（2026-09-21 实测，遗留 L2）】
    `agent/audit/logger.py:209` 的 `audit_logger = AuditLogger()` 是**模块级单例**：
      - `log_dir` 在 import 期固定为默认值 `"./data/audit/"`；
      - `chain_db_path = <log_dir>/audit_chain.db` 作为**显式实参**传给 `AuditFacade(...)`；
      - 而 `AuditFacade.__init__:198` 的路径优先级是
            db_path（显式实参） > AUDIT_DB_PATH（环境变量） > DEFAULT_DB_PATH
      ⇒ **显式实参压过环境变量**，该单例既不读 `AUDIT_DB_PATH`，也不经过 `facade.audit`。
    实测代价：`tests/unit/test_audit_logger_comprehensive.py` 里的
    `audit_logger.log("global_test_action")` **每跑一次就往生产审计链 +1 条**，
    并往生产旧轨 `data/audit/audit_2026MMDD.jsonl` 追加一行。
"""
from __future__ import annotations

from pathlib import Path

_PROD_AUDIT_DIR = Path(__file__).resolve().parents[2] / "data" / "audit"


def _underside(path: Path, parent: Path) -> bool:
    p, q = path.resolve(), parent.resolve()
    return p == q or q in p.parents


def test_module_level_audit_singleton_redirected_off_production():
    """`agent.audit.logger.audit_logger` 的四个路径绑定必须已被会话级守卫改到临时目录"""
    from agent.audit.logger import audit_logger

    bad = [
        f"{attr}={Path(str(getattr(audit_logger, attr))).resolve()}"
        for attr in ("_log_dir", "_current_file", "_chain_db_path", "_roots_path")
        if _underside(Path(str(getattr(audit_logger, attr))), _PROD_AUDIT_DIR)
    ]
    assert not bad, (
        "模块级审计单例仍指向仓库生产审计目录 ⇒ 任何直接调 "
        "`agent.audit.logger.audit_logger.log(...)` 的测试都会污染生产链"
        "（append-only 哈希链，删记录会破坏 prev_hash/self_hash，只能重建）。\n"
        f"命中：{bad}\n"
        "→ 守卫在 tests/conftest.py::_isolate_approval_stores 的「模块级审计单例」段，"
        "请勿删除或提前于该夹具导入 agent.audit.logger。"
    )


def test_facade_db_path_redirected_off_production():
    """`agent.audit.facade.audit`（TASK-06 收敛的唯一入口）的台账路径也不得指向生产库"""
    from agent.audit import facade as facade_mod

    got = Path(str(facade_mod.audit._db_path)).resolve()
    assert not _underside(got, _PROD_AUDIT_DIR), (
        f"审计门面单例 `facade.audit._db_path` 指向生产审计面：{got}\n"
        "→ 见 tests/conftest.py 的会话级隔离（AUDIT_DB_PATH 环境变量 + 重绑门面三路径）。"
    )

# ════════════════════════════════════════════════════════════
#  AUDIT-ROOT-REPAIR（2026-09-26）：每日 Merkle 根的构造级隔离
# ════════════════════════════════════════════════════════════

def _prod_roots() -> Path:
    return _PROD_AUDIT_DIR / "daily_roots.jsonl"


def _stat(p: Path) -> str:
    """(size/mtime_ns) 差集口径（与 tests/conftest.py 的 stray 守卫同一判据）"""
    try:
        st = p.stat()
        return "%d/%d" % (st.st_size, st.st_mtime_ns)
    except OSError:
        return "absent"


def test_audit_roots_env_is_session_isolated():
    """会话级守卫必须把 `AUDIT_ROOTS_PATH` 也指向临时目录（只读断言）

    Why：`AuditChain(db)` 现在按 `roots_path or AUDIT_ROOTS_PATH or DEFAULT_ROOTS_PATH`
    取值 ⇒ 这个 env 就是"直接构造的链往哪儿写日根"的唯一开关。它若指向
    `<repo>/data/audit`，任何测试里的 auto_seal 都会往生产封印日志追加。
    """
    import os

    got = os.environ.get("AUDIT_ROOTS_PATH", "")
    assert got, ("会话级守卫没有设置 AUDIT_ROOTS_PATH ⇒ 直接构造的链会回落到生产 "
                 "data/audit/daily_roots.jsonl（见 tests/conftest.py 的隔离段）")
    assert not _underside(Path(got), _PROD_AUDIT_DIR), (
        f"AUDIT_ROOTS_PATH 指向生产审计目录：{got}\n"
        "→ tests/conftest.py 用的是 os.environ.setdefault ⇒ **继承来的 env 会压过隔离**；"
        "请清掉外部注入的该变量后重跑。")


def test_ctor_honours_env_roots_path(monkeypatch, tmp_path):
    """直接构造 `AuditChain(db)` 必须与门面同口径地尊重 `AUDIT_ROOTS_PATH`

    回归护栏：这一条红了就说明 `chain.py` 的 `self._roots_path` 又变回了
    `roots_path or DEFAULT_ROOTS_PATH`（门面读 env、构造器不读 = 同一语义两个入口）。
    """
    from agent.audit.chain import AuditChain

    env_roots = tmp_path / "env_daily_roots.jsonl"
    monkeypatch.setenv("AUDIT_ROOTS_PATH", str(env_roots))
    chain = AuditChain(str(tmp_path / "ctor.db"), signing_enabled=False,
                       auto_seal=False, auto_start_writer=False,
                       lock_enabled=False, enforce_single_writer=False)
    try:
        bound = Path(str(chain._roots_path)).resolve()
        assert bound == env_roots.resolve(), (
            f"直接构造的链没跟随 AUDIT_ROOTS_PATH：绑定到了 {bound}")
        assert not _underside(bound, _PROD_AUDIT_DIR), f"绑定落回生产目录：{bound}"
    finally:
        chain.close(timeout=2.0)


def test_auto_seal_of_test_chain_never_touches_production_roots(monkeypatch, tmp_path):
    """测试式小链的 auto_seal 只能写隔离日根文件（复现 2026-09-25 污染形态）

    形态证据：生产 `daily_roots.jsonl` 2026-09-25 的 3 条竞争记录的
    `leaf_count` 是 **2 / 4 / 4**、`first_seq` 全是 **1** —— 那是**测试临时库**的形状
    （该日真值 440 条、seq 72261..72700）。本用例重建"测试小链 + auto_seal"这一通路，
    断言记录只落到 `AUDIT_ROOTS_PATH`，且生产文件的 size/mtime_ns **逐字节口径不变**。
    """
    import json
    import time
    from datetime import datetime, timedelta, timezone

    from agent.audit.chain import AuditChain

    prod_before = _stat(_prod_roots())
    env_roots = tmp_path / "env_daily_roots.jsonl"
    monkeypatch.setenv("AUDIT_ROOTS_PATH", str(env_roots))
    chain = AuditChain(str(tmp_path / "seal.db"), signing_enabled=False,
                       auto_seal=True, auto_start_writer=True,
                       lock_enabled=False, enforce_single_writer=False)
    try:
        # 【顺序要紧】先断言绑定再写：绑定错了要在**写入之前**红，否则本用例自己会污染生产。
        bound = Path(str(chain._roots_path)).resolve()
        assert bound == env_roots.resolve(), f"日根没绑到 AUDIT_ROOTS_PATH：{bound}"
        assert not _underside(bound, _PROD_AUDIT_DIR), f"日根绑到了生产目录：{bound}"

        finished = (datetime.now(timezone.utc).date()
                    - timedelta(days=1)).isoformat()
        ts = finished + "T10:00:00+00:00"
        rows = 3
        for i in range(rows):
            chain.append(action="probe.audit_root_isolation.%d" % i, actor="tester",
                         subject="s:%d" % i, payload={"i": i}, ts=ts)
        chain.flush(timeout=5.0)
        for _ in range(100):                      # 后台 writer 线程异步封存
            if env_roots.exists() and env_roots.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        recs = [json.loads(x) for x in
                env_roots.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert recs, ("auto_seal 没有把「已过完的 UTC 日」的根写进 AUDIT_ROOTS_PATH "
                      "指向的文件 ⇒ 它写去了别处（很可能就是生产文件）")
        root = recs[-1]
        assert root["date"] == finished, root
        assert (root["leaf_count"], root["first_seq"], root["last_seq"]) == (rows, 1, rows), (
            "隔离日根的形态与 2026-09-25 那 3 条污染记录同型（小链 + seq 从 1 起）：%s" % root)
        assert root["prev_entry_hash"] == "0" * 64, "隔离文件里的第一条根应续接创世 prev"
    finally:
        chain.close(timeout=5.0)
    assert _stat(_prod_roots()) == prod_before, (
        "测试链的 auto_seal 改动了**生产**日根文件（size/mtime_ns 变了）\n"
        f"before={prod_before} after={_stat(_prod_roots())}")
