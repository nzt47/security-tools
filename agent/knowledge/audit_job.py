"""定时自动审计：每日执行 lint + 报告落盘 + log.md 登记 + 邮件通知（任务5 · Step 4）。

- 报告落盘：`data/knowledge/reports/knowledge_health_<YYYYMMDD>.md` 与 `.html`。
- log.md 追加：`## [YYYY-MM-DD] audit | health | score=XX.XX`。
- 邮件通知：每日 02:00 巡检后将 HTML 健康报告发送至 SMTP 收件人。
- 调度器不可用（未初始化/异常）时静默跳过，不抛异常（降级铁律）。

【不易】
- 审计任务只读巡检（lint_all 不修改卡片/索引），可安全每日执行。
- 裁决必须由人触发（AGENTS.md §6.2）：本模块**只自动执行 lint + 报告 + 邮件**，
  `resolve_conflict` 仅提供手动 CLI 入口（`python -m agent.knowledge resolve-conflict`）。
- SMTP 配置仅从环境变量读取（.env 为唯一敏感数据源），未配置时静默跳过不发邮件。
- CardStore 采用 importlib 惰性导入（card↔index 循环依赖规避，同 _get_store 模式）。
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from agent.knowledge.lint import lint_all, render_report
from agent.knowledge.logbook import append_log
from agent.knowledge.reporting import render_html_report

logger = logging.getLogger(__name__)

# 报告落盘默认目录（相对项目根：data/knowledge/reports/）
DEFAULT_REPORTS_DIR = Path("data") / "knowledge" / "reports"

# 真实知识库 wiki 根（agent/knowledge/audit_job.py → 上三级 = 项目根/knowledge/wiki）
WIKI_ROOT_DEFAULT = Path(__file__).resolve().parent.parent.parent / "knowledge" / "wiki"


def _get_store(wiki_root: str | Path):
    """importlib 惰性构造 CardStore（无 AST import 边，规避循环依赖）。"""
    from importlib import import_module

    store_cls = import_module("agent.knowledge.card").CardStore
    return store_cls(wiki_root)


def run_knowledge_audit(
    wiki_root: str | Path,
    *,
    index_path: Optional[str | Path] = None,
    log_path: Optional[str | Path] = None,
    reports_dir: Optional[str | Path] = None,
    stale_days: int = 90,
):
    """执行一次完整审计：lint → 报告落盘（md + html）→ log.md 登记。

    参数缺省时使用 CardStore 默认布局（wiki_root 父目录下的 index.md / log.md）。
    返回 HealthReport（调用方可直接检查健康分）。
    """
    store = _get_store(wiki_root)
    index_path = index_path or store._index_path
    log_path = log_path or store._log_path
    reports_dir = reports_dir or DEFAULT_REPORTS_DIR
    reports_dir = Path(reports_dir)

    report = lint_all(store, index_path=index_path, stale_days=stale_days)
    _write_report(report, reports_dir)
    _write_html_report(report, reports_dir)
    append_log(
        "audit", "health", f"score={report.health_score:.2f}", log_path=log_path,
    )
    logger.info(
        "run_knowledge_audit: 完成 wiki_root=%s score=%.2f reports_dir=%s",
        wiki_root, report.health_score, reports_dir,
    )
    return report


def _write_report(report, reports_dir: Path) -> Path:
    """健康报告落盘：data/knowledge/reports/knowledge_health_<YYYYMMDD>.md。"""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"knowledge_health_{date.today().strftime('%Y%m%d')}.md"
    path.write_text(render_report(report), encoding="utf-8")
    logger.info("健康报告已落盘: %s", path)
    return path


def _write_html_report(report, reports_dir: Path) -> Path:
    """HTML 健康报告落盘：knowledge_health_<YYYYMMDD>.html（含可视化图表）。"""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"knowledge_health_{date.today().strftime('%Y%m%d')}.html"
    path.write_text(render_html_report(report), encoding="utf-8")
    logger.info("HTML 健康报告已落盘: %s", path)
    return path


# SMTP 邮件配置项（.env 环境变量，唯一敏感数据源）
_MAIL_FROM_ENV = "MAIL_FROM"
_MAIL_RECIPIENTS_ENV = "MAIL_RECIPIENTS"  # 逗号分隔


def send_knowledge_report_email(
    report,
    *,
    html: Optional[str] = None,
    smtp_host: Optional[str] = None,
    smtp_port: Optional[int] = None,
    smtp_username: Optional[str] = None,
    smtp_password: Optional[str] = None,
    mail_from: Optional[str] = None,
    recipients: Optional[list[str]] = None,
) -> bool:
    """发送 HTML《知识库健康报告》邮件（SMTP + starttls）。

    参数缺省时从环境变量读取：SMTP_HOST / SMTP_PORT / SMTP_USERNAME /
    SMTP_PASSWORD / MAIL_FROM / MAIL_RECIPIENTS（逗号分隔）。
    - 未配置 SMTP_HOST 或无收件人 → 静默返回 False（不发邮件，降级铁律）。
    - 发送失败仅记 warning 返回 False，不抛异常（不影响任务状态）。
    """
    host = smtp_host or os.getenv("SMTP_HOST")
    port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
    username = smtp_username if smtp_username is not None else os.getenv("SMTP_USERNAME")
    password = smtp_password if smtp_password is not None else os.getenv("SMTP_PASSWORD")
    mail_from = mail_from or os.getenv(_MAIL_FROM_ENV) or username
    to_list = recipients or [
        r.strip() for r in os.getenv(_MAIL_RECIPIENTS_ENV, "").split(",") if r.strip()
    ]
    if not host or not to_list:
        logger.info(
            "send_knowledge_report_email: SMTP 未配置（host=%r recipients=%d），静默跳过",
            host, len(to_list),
        )
        return False

    body_html = html or render_html_report(report)
    subject = (
        f"【知识库健康报告】{report.checked_at} 健康分 {report.health_score:.1f}"
    )
    # 纯文本摘要作为替代正文（邮件客户端兼容）
    body_text = (
        f"知识库健康报告（{report.checked_at}）\n"
        f"健康分: {report.health_score:.1f}/100\n"
        f"孤儿卡片: {len(report.orphans)} 条；断链: {len(report.broken_links)} 条；"
        f"index 漂移: {len(report.index_drift)} 张；过期声明: {len(report.stale_cards)} 条；"
        f"未裁决矛盾: {len(report.unresolved_conflicts)} 条。\n\n"
        "详见附件 HTML 报告或 data/knowledge/reports/ 下的完整报告文件。"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            if username:
                server.login(username, password or "")
            server.sendmail(mail_from, to_list, msg.as_string())
        logger.info(
            "send_knowledge_report_email: 已发送至 %d 个收件人（score=%.1f）",
            len(to_list), report.health_score,
        )
        return True
    except Exception as exc:  # 降级铁律：邮件失败不影响任务状态
        logger.warning(
            "send_knowledge_report_email: 发送失败已降级 host=%s 原因=%r",
            host, exc,
        )
        return False


def _audit_runner() -> None:
    """调度器无参适配：对真实知识库执行一次完整审计并发送健康报告邮件。

    TaskScheduler.add_cron_task 执行任务时以 `func()` 无参调用，
    而 run_knowledge_audit 需要 wiki_root 参数，故包装为闭包。
    审计成功（report 非空）后自动发送邮件；失败时不发（邮件需有内容可发）。
    """
    report = run_knowledge_audit(WIKI_ROOT_DEFAULT)
    if report is not None:
        send_knowledge_report_email(report)


def register_knowledge_audit_job(
    scheduler,
    interval_days: int = 1,
    run_hour: int = 2,
) -> bool:
    """向任务调度器注册每日审计任务（默认每日 02:00 执行）。

    调度器为 None 或注册抛异常时静默返回 False（降级铁律：不抛异常）。
    注：task_scheduler 的 add_cron_task 仅支持每天粒度（day_of_week=None），
    interval_days 参数保留用于语义描述，实际按每日 cron 注册。
    注册的执行体为 `_audit_runner`（无参包装，调度器 `func()` 可直接调用）。
    """
    if scheduler is None:
        logger.info("register_knowledge_audit_job: 调度器未初始化，静默跳过")
        return False
    try:
        scheduler.add_cron_task(
            "knowledge_audit", _audit_runner,
            hour=run_hour, minute=0,
        )
        logger.info(
            "register_knowledge_audit_job: 已注册每日 %02d:00 审计任务",
            run_hour,
        )
        return True
    except Exception as exc:  # 降级铁律：调度器异常不阻断主流程
        logger.warning("register_knowledge_audit_job: 注册失败已降级: %r", exc)
        return False
