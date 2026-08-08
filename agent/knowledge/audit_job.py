"""知识库定时自动审计（任务5 · 治理层）：lint → 健康报告落盘 → log.md 登记。

- 复用 `agent/task_scheduler.py` 注册每日定时任务（python_func cron）。
- 报告落盘：`data/knowledge/reports/knowledge_health_<YYYYMMDD>.md`。
- 执行完追加 log.md：`## [YYYY-MM-DD] audit | health | score=XX.XX`。
- 调度器不可用（未初始化/注册异常）时静默跳过，不抛异常（项目降级铁律）。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from agent.knowledge.lint import HealthReport, lint_all, render_report
from agent.knowledge.logbook import append_log

logger = logging.getLogger(__name__)

DEFAULT_REPORTS_DIR = Path("data") / "knowledge" / "reports"


def _write_report(report: HealthReport, reports_dir: str | Path,
                  *, stale_days: int) -> Path:
    """健康报告落盘 data/knowledge/reports/knowledge_health_<YYYYMMDD>.md。"""
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"knowledge_health_{date.today().strftime('%Y%m%d')}.md"
    path.write_text(render_report(report, stale_days=stale_days), encoding="utf-8")
    logger.info("知识库健康报告已落盘: %s", path)
    return path


def run_knowledge_audit(
    wiki_root: str | Path = "knowledge/wiki",
    *,
    index_path: Optional[str | Path] = None,
    log_path: Optional[str | Path] = None,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
    stale_days: int = 90,
) -> HealthReport:
    """执行一次完整审计：lint → 报告落盘 → log.md 登记。

    默认路径布局与 CardStore 一致（index.md / log.md 位于 wiki_root 父目录）。
    """
    from agent.knowledge.card import CardStore  # importlib 惰性导入（无 AST 循环边）

    store = CardStore(wiki_root)
    index_path = Path(index_path) if index_path else store._index_path
    log_path = Path(log_path) if log_path else store._log_path

    report = lint_all(store, index_path=index_path, stale_days=stale_days)
    _write_report(report, reports_dir, stale_days=stale_days)
    append_log(
        "audit", "health", f"score={report.health_score:.2f}",
        log_path=log_path,
    )
    logger.info(
        "run_knowledge_audit: score=%.2f total=%d reports_dir=%s",
        report.health_score, report.total_cards, reports_dir,
    )
    return report


def register_knowledge_audit_job(
    scheduler,
    interval_days: int = 1,
    run_hour: int = 3,
) -> bool:
    """注册每日 run_hour:00 的知识库自动审计定时任务。

    - 复用 `agent/task_scheduler.py::add_cron_task`（python_func cron）。
    - 调度器不可用（None / 注册异常）时静默跳过并返回 False，不抛异常。
    - 注意：task_scheduler 的 cron 仅支持「每天」粒度（day_of_week=None），
      `interval_days` 参数保留用于扩展；>1 时按每日执行（如实记录）。
    """
    if scheduler is None:
        logger.info("register_knowledge_audit_job: 调度器未初始化，静默跳过")
        return False
    try:
        scheduler.add_cron_task(
            "knowledge_audit", run_knowledge_audit, hour=run_hour, minute=0,
        )
        logger.info(
            "register_knowledge_audit_job: 已注册每日 %02d:00 知识库审计",
            run_hour,
        )
        return True
    except Exception as exc:  # 降级铁律：注册失败不抛异常
        logger.warning("register_knowledge_audit_job: 注册失败，静默跳过: %r", exc)
        return False
