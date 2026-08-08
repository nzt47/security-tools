"""任务5 · Lint 健康巡检 + 定时审计 回归测试

覆盖（评估标准）：
- 五类检测项（孤儿/断链/index 漂移/过期声明/未裁决矛盾）每类独立断言。
- 健康分边界：空库 100 / 全问题 0 / 扣分封顶 / 问题越多分越低。
- 定时审计：注册成功、调度器缺失/异常静默跳过、报告落盘 + log.md 登记、
  单次执行 < 5s（小库）。
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from agent.knowledge.audit_job import (
    DEFAULT_REPORTS_DIR,
    register_knowledge_audit_job,
    run_knowledge_audit,
)
from agent.knowledge.card import CardStore
from agent.knowledge.lint import (
    HealthReport,
    compute_health_score,
    lint_all,
    render_report,
)
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "卡片",
    slug: str = "",
    status: str = "current",
    type: str = "concepts",
    content: str = "",
    links=None,
    contradictions=None,
    date_str: str = "",
    insight: str = "一句话核心洞见",
) -> Card:
    card = Card(
        title=title,
        slug=slug or slugify(title),
        status=status,
        type=type,
        source="inbox/test.md",
        date=date_str or date.today().isoformat(),
        tags=[],
        links=links if links is not None else [],
        contradictions=contradictions if contradictions is not None else [],
        insight=insight,
    )
    card.content = content
    return card


@pytest.fixture
def kb(tmp_path):
    """临时知识库：返回 (store, index_path, log_path)。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    return store, root / "index.md", root / "log.md"


def _drop_index_entry(index_path: Path, slug: str) -> None:
    """手工篡改 index.md：移除指定 slug 条目（模拟外部编辑/漂移）。"""
    text = index_path.read_text(encoding="utf-8")
    kept = [l for l in text.splitlines() if f"[[{slug}]]" not in l]
    index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")


# ---------- 五类检测项（每类独立断言） ----------


def test_lint_empty_library_score_100(kb):
    store, index_path, _ = kb
    report = lint_all(store, index_path=index_path)
    assert report.total_cards == 0
    assert report.orphans == []
    assert report.broken_links == []
    assert report.index_drift == []
    assert report.stale_cards == []
    assert report.unresolved_conflicts == []
    assert report.health_score == 100.0


def test_lint_detects_orphans(kb):
    store, index_path, _ = kb
    store.create(make_card("Alpha", slug="alpha"))
    report = lint_all(store, index_path=index_path)
    assert report.orphans == ["alpha"]
    assert report.health_score == 98.0  # 1 孤儿扣 2


def test_lint_detects_broken_links(kb):
    store, index_path, _ = kb
    store.create(make_card("A", slug="a", content="见 [[ghost]]", links=["ghost"]))
    store.create(make_card("B", slug="b", links=["a"]))
    report = lint_all(store, index_path=index_path)
    assert report.broken_links == [{"from_slug": "a", "to_slug": "ghost"}]
    # 断链扣 2（b→a 使 a 非孤儿，b 为孤儿扣 2）
    assert report.health_score == 96.0


def test_lint_detects_index_drift(kb):
    store, index_path, _ = kb
    # 互链消除孤儿副作用：漂移扣分是唯一扣分项
    store.create(make_card("Alpha", slug="alpha", links=["beta"]))
    store.create(make_card("Beta", slug="beta", links=["alpha"]))
    _drop_index_entry(index_path, "alpha")
    report = lint_all(store, index_path=index_path)
    assert report.index_drift == ["alpha"]
    assert report.health_score == 98.0  # 1 漂移扣 2


def test_lint_detects_index_ghost_entry(kb):
    """反向漂移：index.md 存在卡片集合之外的条目。"""
    store, index_path, _ = kb
    store.create(make_card("Alpha", slug="alpha"))
    with index_path.open("a", encoding="utf-8") as f:
        f.write("- [[ghost]] `current` — 幽灵条目\n")
    report = lint_all(store, index_path=index_path)
    assert "ghost" in report.index_drift
    assert "alpha" not in report.index_drift


def test_lint_detects_stale_cards(kb):
    store, index_path, _ = kb
    old = date.today() - timedelta(days=200)
    # 互链消除孤儿副作用：过期扣分是唯一扣分项
    store.create(make_card("Stale", slug="stale", date_str=old.isoformat(), links=["fresh"]))
    store.create(make_card("Fresh", slug="fresh", links=["stale"]))
    report = lint_all(store, index_path=index_path, stale_days=90)
    assert report.stale_cards == [{"slug": "stale", "days_unaccessed": 200}]
    assert report.health_score == 97.0  # 1 过期扣 3


def test_lint_stale_skips_non_current(kb):
    store, index_path, _ = kb
    old = date.today() - timedelta(days=200)
    store.create(make_card("OldDraft", slug="olddraft", status="draft", date_str=old.isoformat()))
    report = lint_all(store, index_path=index_path, stale_days=90)
    assert report.stale_cards == []


def test_lint_detects_unresolved_conflicts(kb):
    store, index_path, _ = kb
    store.create(make_card("A", slug="a", links=["b", "c"], contradictions=[
        {"target_slug": "b", "status": "conflict", "summary": "观点相悖"},
    ]))
    store.create(make_card("B", slug="b", links=["a"]))
    store.create(make_card("C", slug="c", links=["a"], contradictions=[
        {"target_slug": "x", "status": "resolved", "summary": "已裁决"},
    ]))
    report = lint_all(store, index_path=index_path)
    assert report.unresolved_conflicts == [
        {"source_slug": "a", "target_slug": "b", "summary": "观点相悖"},
    ]
    assert report.health_score == 95.0  # 1 未裁决扣 5


def test_lint_all_detects_all_five(kb):
    """五类问题同库出现，每类独立命中 + 健康分递减。"""
    store, index_path, _ = kb
    store.create(make_card("A", slug="a", content="见 [[ghost]]", links=["ghost"]))
    store.create(make_card("B", slug="b", links=["a"]))
    old = date.today() - timedelta(days=200)
    store.create(make_card("C", slug="c", date_str=old.isoformat()))
    store.create(make_card("D", slug="d", contradictions=[
        {"target_slug": "x", "status": "conflict", "summary": "s"},
    ]))
    _drop_index_entry(index_path, "a")

    report = lint_all(store, index_path=index_path, stale_days=90)
    assert report.total_cards == 4
    assert report.orphans == ["b", "c", "d"]          # A 被 B 引用非孤儿
    assert report.broken_links == [{"from_slug": "a", "to_slug": "ghost"}]
    assert report.index_drift == ["a"]                # 条目被手工删除
    assert report.stale_cards == [{"slug": "c", "days_unaccessed": 200}]
    assert report.unresolved_conflicts == [
        {"source_slug": "d", "target_slug": "x", "summary": "s"},
    ]
    # 6 + 2 + 2 + 3 + 5 = 18 → 82.0
    assert report.health_score == 82.0


def test_lint_missing_index_file_counts_all_as_drift(kb):
    """index.md 缺失时全部卡片视为漂移（索引未建立）。"""
    store, index_path, _ = kb
    store.create(make_card("Alpha", slug="alpha"))
    index_path.unlink()
    report = lint_all(store, index_path=index_path)
    assert report.index_drift == ["alpha"]
    assert report.health_score == 96.0  # 1 孤儿 2 + 1 漂移 2


# ---------- 健康分边界与扣分封顶 ----------


def test_compute_health_score_clean_is_100():
    r = HealthReport(checked_at="2026-01-01", total_cards=10)
    assert compute_health_score(r) == 100.0


def test_compute_health_score_orphan_cap():
    r = HealthReport(checked_at="2026-01-01", total_cards=30)
    r.orphans = [f"o{i}" for i in range(30)]  # 30*2=60 → 封顶 20
    assert compute_health_score(r) == 80.0


def test_compute_health_score_each_cap():
    """每类扣分封顶独立生效。"""
    r = HealthReport(checked_at="2026-01-01", total_cards=100)
    r.broken_links = [{} for _ in range(30)]  # 封顶 20
    assert compute_health_score(r) == 80.0

    r2 = HealthReport(checked_at="2026-01-01", total_cards=100)
    r2.index_drift = [f"d{i}" for i in range(20)]  # 20*2=40 → 封顶 10
    assert compute_health_score(r2) == 90.0

    r3 = HealthReport(checked_at="2026-01-01", total_cards=100)
    r3.stale_cards = [{} for _ in range(20)]  # 20*3=60 → 封顶 20
    assert compute_health_score(r3) == 80.0

    r4 = HealthReport(checked_at="2026-01-01", total_cards=100)
    r4.unresolved_conflicts = [{} for _ in range(20)]  # 20*5=100 → 封顶 30
    assert compute_health_score(r4) == 70.0


def test_compute_health_score_lower_bound_zero():
    """扣分累计达 100 时封底为 0.0（不出现负分）。"""
    r = HealthReport(checked_at="2026-01-01", total_cards=200)
    r.orphans = [f"o{i}" for i in range(30)]  # 封顶 20
    r.broken_links = [{} for _ in range(30)]  # 封顶 20
    r.index_drift = [f"d{i}" for i in range(30)]  # 封顶 10
    r.stale_cards = [{} for _ in range(30)]  # 封顶 20
    r.unresolved_conflicts = [{} for _ in range(30)]  # 封顶 30
    assert compute_health_score(r) == 0.0


# ---------- 建议与报告渲染 ----------


def test_suggestions_healthy_and_issues(kb):
    store, index_path, _ = kb
    assert lint_all(store, index_path=index_path).suggestions == ["知识库状态良好，无需处理"]

    store.create(make_card("Alpha", slug="alpha"))
    report = lint_all(store, index_path=index_path)
    assert any("孤儿" in s for s in report.suggestions)


def test_render_report_contains_sections(kb):
    store, index_path, _ = kb
    store.create(make_card("Alpha", slug="alpha"))
    text = render_report(lint_all(store, index_path=index_path), stale_days=90)
    assert "知识库健康报告" in text
    assert "健康分" in text
    assert "一、孤儿卡片" in text
    assert "二、断链" in text
    assert "三、index 漂移" in text
    assert "四、过期声明" in text
    assert "五、未裁决矛盾" in text
    assert "六、建议" in text
    assert "- alpha" in text


# ---------- 定时自动审计（Step 4） ----------


class _FakeScheduler:
    """最小化调度器替身：仅记录 add_cron_task 调用。"""

    def __init__(self):
        self.tasks = []

    def add_cron_task(self, name, func, day_of_week=None, hour=0, minute=0):
        self.tasks.append((name, func, day_of_week, hour, minute))


def test_register_audit_job_success():
    sched = _FakeScheduler()
    assert register_knowledge_audit_job(sched, run_hour=3) is True
    name, func, _, hour, minute = sched.tasks[0]
    assert name == "knowledge_audit"
    assert hour == 3 and minute == 0
    assert callable(func)


def test_register_audit_job_none_scheduler_silent():
    assert register_knowledge_audit_job(None) is False  # 不抛异常


def test_register_audit_job_scheduler_error_silent():
    class _Boom:
        def add_cron_task(self, *a, **kw):
            raise RuntimeError("scheduler broken")

    assert register_knowledge_audit_job(_Boom()) is False  # 降级：静默跳过


def test_run_audit_writes_report_and_log_fast(kb, tmp_path):
    store, index_path, log_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    reports_dir = tmp_path / "reports"
    t0 = time.perf_counter()
    report = run_knowledge_audit(
        store._wiki_root,
        index_path=index_path,
        log_path=log_path,
        reports_dir=reports_dir,
    )
    elapsed = time.perf_counter() - t0

    assert report.total_cards == 1
    assert report.health_score == 98.0  # 1 孤儿
    assert (reports_dir / f"knowledge_health_{date.today().strftime('%Y%m%d')}.md").exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert f"audit | health | score={report.health_score:.2f}" in log_text
    assert elapsed < 5.0  # 小库单次执行 < 5s


def test_run_audit_default_reports_dir(tmp_path, monkeypatch):
    """默认报告目录 data/knowledge/reports/ 落盘。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    store.create(make_card("Alpha", slug="alpha"))
    monkeypatch.chdir(tmp_path)  # 相对路径 data/... 解析到 tmp_path 下
    report = run_knowledge_audit(
        root / "wiki",
        index_path=store._index_path,
        log_path=store._log_path,
    )
    expected = tmp_path / DEFAULT_REPORTS_DIR / (
        f"knowledge_health_{date.today().strftime('%Y%m%d')}.md"
    )
    assert expected.exists()
    assert report.health_score == 98.0
