"""生产审计面隔离的**会话级守卫**回归（TASK-A / 遗留 L2）

【本文件为什么单独存在】
    `tests/unit/test_audit_logger_comprehensive.py` 自己带一个 autouse 夹具，把模块级单例
    重绑到该用例的 `tmp_path` ⇒ 在那个文件里**无法**检出「`tests/conftest.py` 的会话级
    守卫被移除」这件事（局部夹具会把它掩盖掉）。本文件**不带**局部夹具，只依赖会话级守卫，
    因此它的通过等价于"守卫在位"。

【为什么本文件刻意只做只读断言】
    若守卫被移除，本文件的断言会失败 —— 此时若它还带写操作，就会**先污染生产链再报红**。
    故写型断言一律留在有 `tmp_path` 兜底的文件里；本文件只读路径绑定与生产库。

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
