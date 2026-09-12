"""TASK-S8-01 调度单测（验收#6：默认关闭 + 首跑 dry-run + 每次执行入链式审计）。"""

from __future__ import annotations

import os

import pytest

from agent.retention.archiver import AUDIT_ACTION, Archiver
from agent.retention.policy import (
    ENV_DELETE_SOURCE,
    ENV_DRY_RUN,
    ENV_ENABLED,
    load_policy,
)
from agent.retention.scheduler import DEFAULT_SCHEDULE, TASK_NAME, register_retention_job

from retention_testkit import (       # noqa: E402
    drafts_class,
    fixed_clock,
    make_policy,
    make_root,
    touch_old,
)


class FakeScheduler:
    """最小 `TaskScheduler` 替身（只记录注册了什么）。"""

    def __init__(self):
        self.tasks = []

    def add_cron_task(self, name, func, day_of_week=None, hour=0, minute=0):
        self.tasks.append({"name": name, "func": func, "task_id": f"py-{len(self.tasks)}",
                           "cron": {"day_of_week": day_of_week, "hour": hour,
                                    "minute": minute}})


class BrokenScheduler:
    def add_cron_task(self, **kwargs):
        raise RuntimeError("调度器坏了")


def _draft(root: str, i: int = 0) -> str:
    path = os.path.normpath(os.path.join(root, "data", "digestion", "drafts",
                                         f"dig-{i}", "SKILL.md"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# draft {i}\n")
    touch_old(path, "2026-01-01")
    return path


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path):
    """把审计链**显式注入**为临时库（绝不写工作区 `data/audit/`）。

    【不易】不能用 `AUDIT_DB_PATH` 环境变量做隔离：进程级 `audit = AuditFacade()`
    在 `agent.audit.facade` **导入时**就已构造并读走了环境（`reset_audit_facade()`
    也不重新读环境），故后来设置的环境变量无效 —— 那会让用例悄悄写进**真实链**。
    正确做法是 `audit.bind(chain)` 注入临时台账，用例结束后还原。
    """
    from agent.audit.chain import AuditChain
    from agent.audit.facade import audit

    base = tmp_path / "audit_home"
    base.mkdir(parents=True, exist_ok=True)
    chain = AuditChain(db_path=str(base / "audit_chain.db"),
                       roots_path=str(base / "daily_roots.jsonl"),
                       signing_key_path=str(base / "signing_key.pem"))
    previous = audit.bind(chain)
    yield chain
    audit.bind(previous)
    try:
        chain.close()
    except Exception:  # noqa: BLE001 收尾失败不影响结论
        pass


# ── 1. 默认关闭 ──────────────────────────────────────────────


def test_default_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    sched = FakeScheduler()
    result = register_retention_job(sched)
    assert result["status"] == "disabled"
    assert sched.tasks == [], "默认关闭却注册了任务"
    assert "CP_RETENTION_ENABLED" in result["note"]


def test_disabled_does_not_even_build_a_scheduler(tmp_path, monkeypatch):
    """关闭时连 `get_scheduler()` 都不该被调用（不产生任何副作用）。"""
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    import agent.task_scheduler as ts

    called = {"n": 0}
    monkeypatch.setattr(ts, "get_scheduler",
                        lambda: called.__setitem__("n", called["n"] + 1))
    result = register_retention_job(None)
    assert result["status"] == "disabled"
    assert called["n"] == 0


def test_enabled_registers_cron_with_expected_defaults(tmp_path):
    root = make_root(tmp_path)
    policy = make_policy(root, [drafts_class()], enabled=True)
    sched = FakeScheduler()
    result = register_retention_job(sched, policy=policy)
    assert result["status"] == "scheduled"
    assert result["first_run_forced_dry_run"] is True
    assert result["delete_source"] is False
    task = sched.tasks[0]
    assert task["name"] == TASK_NAME
    assert task["cron"] == {"day_of_week": DEFAULT_SCHEDULE["day_of_week"],
                            "hour": DEFAULT_SCHEDULE["hour"],
                            "minute": DEFAULT_SCHEDULE["minute"]}
    assert task["cron"]["day_of_week"] == 6, "默认每周日"


def test_schedule_can_be_overridden(tmp_path):
    root = make_root(tmp_path)
    policy = make_policy(root, [drafts_class()], enabled=True)
    sched = FakeScheduler()
    register_retention_job(sched, policy=policy, day_of_week=2, hour=5, minute=30)
    assert sched.tasks[0]["cron"] == {"day_of_week": 2, "hour": 5, "minute": 30}


def test_registration_failure_does_not_raise(tmp_path):
    root = make_root(tmp_path)
    policy = make_policy(root, [drafts_class()], enabled=True)
    result = register_retention_job(BrokenScheduler(), policy=policy)
    assert result["status"] == "error"
    assert "调度器坏了" in result["error"]


def test_env_enabled_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    sched = FakeScheduler()
    policy = load_policy(env={ENV_ENABLED: "true"}, classes=[drafts_class()])
    policy.archive_dir = os.path.join(str(tmp_path), "data", "archive")
    result = register_retention_job(sched, policy=policy, archiver=None)
    assert result["status"] == "scheduled"


# ── 2. 首跑强制 dry-run ──────────────────────────────────────


def test_first_run_is_forced_dry_run_even_when_dry_run_disabled(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    policy = make_policy(root, [drafts_class()], enabled=True, dry_run=False)
    sched = FakeScheduler()
    register_retention_job(sched, policy=policy,
                           archiver=Archiver(policy, root=root,
                                             clock=fixed_clock(), audit=False,
                                             emit_events=False))
    tick = sched.tasks[0]["func"]

    first = tick()
    assert first["status"] == "ok"
    assert first["dry_run"] is True and first["first_run_forced_dry_run"] is True
    assert not os.path.exists(os.path.join(root, "data", "archive")), \
        "首跑竟然落盘了"

    second = tick()
    assert second["dry_run"] is False
    assert second["first_run_forced_dry_run"] is False
    assert os.path.isdir(os.path.join(root, "data", "archive")), \
        "第二次运行应当真正归档"
    assert os.path.exists(draft), "默认只归档不删除"


def test_tick_never_raises(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    policy = make_policy(root, [drafts_class()], enabled=True)
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    monkeypatch.setattr(box, "plan", lambda: (_ for _ in ()).throw(RuntimeError("炸")))
    sched = FakeScheduler()
    register_retention_job(sched, policy=policy, archiver=box)
    result = sched.tasks[0]["func"]()
    assert result["status"] == "error"
    assert "炸" in result["error"]


# ── 3. 每次执行入链式审计（含条数与体积）──────────────────────


def test_execution_writes_chained_audit_with_counts_and_bytes(tmp_path, isolated_audit):
    root = make_root(tmp_path)
    _draft(root, 0)
    _draft(root, 1)
    policy = make_policy(root, [drafts_class()], enabled=True, dry_run=False)
    sched = FakeScheduler()
    register_retention_job(sched, policy=policy,
                           archiver=Archiver(policy, root=root,
                                             clock=fixed_clock(), audit=True,
                                             emit_events=False))
    tick = sched.tasks[0]["func"]
    tick()                       # 首跑 dry-run：不应写审计（未落盘）
    result = tick()              # 第二次：真正归档 + 入链

    assert result["status"] == "ok" and result["dry_run"] is False
    audit = result["audit"]
    assert audit["status"] == "recorded", audit
    assert audit["flushed"] is True, "审计必须显式 flush（CLI 进程很快就退出）"
    payload = audit["payload"]
    assert payload["archived_files"] == 2
    assert payload["archived_records"] == 2
    assert payload["archived_bytes"] > 0
    assert payload["deleted_files"] == 0
    assert payload["dry_run"] is False

    # 链上确实有 action=retention.run，且链可验证
    from agent.audit import get_audit

    facade = get_audit()
    entries = facade.chain.entries(action=AUDIT_ACTION)
    assert len(entries) == 1, "retention.run 未入链（或入了多次）"
    assert entries[0].actor == "retention"
    # 链上载荷按 `audit.chain.v1` 形状包在 `payload` 键内
    chained = entries[0].payload["payload"]
    assert chained["archived_files"] == 2
    assert chained["archived_records"] == 2
    assert chained["archived_bytes"] > 0
    assert facade.verify().ok is True


def test_audit_entry_is_durable_for_an_independent_reader(tmp_path, isolated_audit):
    """**落盘**而非只在内存/待写缓冲里：换一个独立只读链也读得到该条。

    这是"每次执行入链式审计"的可判定形式。链的写入是后台批量线程 + 进程退出即止，
    不显式 flush 会丢掉最后一批（S8-01 实测：连续两次 CLI 执行只留下 1 条）。
    """
    from agent.audit.chain import AuditChain

    root = make_root(tmp_path)
    _draft(root)
    policy = make_policy(root, [drafts_class()], enabled=True, dry_run=False)
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=True,
                   emit_events=False)
    report = box.run(confirm=True)
    assert report.audit["status"] == "recorded"

    db = str(tmp_path / "audit_home" / "audit_chain.db")
    reader = AuditChain.reader(db_path=db)
    try:
        entries = reader.entries(action=AUDIT_ACTION)
        assert len(entries) == 1, "独立只读链看不到该条 ⇒ 审计其实没落盘"
        assert entries[0].actor == "retention"
        assert reader.verify_chain().ok is True
    finally:
        reader.close()


def test_audit_failure_does_not_break_execution(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    policy = make_policy(root, [drafts_class()], enabled=True, dry_run=False)
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=True,
                   emit_events=False)
    # 让审计入口不可用（模拟链故障）
    import agent.audit as audit_pkg

    original = audit_pkg.record
    audit_pkg.record = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("链挂了"))
    try:
        report = box.run(confirm=True)
    finally:
        audit_pkg.record = original
    assert report.totals["archives"] == 1, "归档应已完成"
    assert report.audit["status"] == "error"


def test_env_delete_source_still_requires_guard(tmp_path):
    """总开关打开也要过护栏：非可删类（events）仍不会被删。"""
    root = make_root(tmp_path)
    policy = make_policy(root, [drafts_class()], enabled=True,
                         delete_source=True)
    sched = FakeScheduler()
    register_retention_job(sched, policy=policy)
    assert sched.tasks and sched.tasks[0]["name"] == TASK_NAME
    assert load_policy(env={ENV_DELETE_SOURCE: "true"}).delete_source is True
    assert load_policy(env={ENV_DRY_RUN: "false"}).dry_run is False
