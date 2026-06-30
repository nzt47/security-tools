#!/usr/bin/env python3
"""架构违规自动通知与监控记录脚本

当架构规则校验检测到未豁免违规时，自动：
1. 记录结构化日志到监控系统（JSON 格式，含 trace_id/module_name/action/duration_ms）
2. 发送 Webhook 通知（支持 Slack / Microsoft Teams / 自定义 Webhook）
3. 输出 Prometheus 指标文件（供 Prometheus 抓取）
4. 生成违规事件归档文件（供后续审计）

使用方式：
    # 本地运行（读取 arch_rules_report.json）
    python scripts/notify_arch_violation.py

    # CI 中运行（通过环境变量获取 PR 信息）
    python scripts/notify_arch_violation.py \\
        --report docs/architecture/arch_rules_report.json \\
        --webhook-url https://hooks.slack.com/services/xxx \\
        --pr-number ${{ github.event.pull_request.number }} \\
        --pr-url ${{ github.event.pull_request.html_url }} \\
        --repo ${{ github.repository }}

环境变量：
    ARCH_WEBHOOK_URL: Webhook 通知地址（也可用 --webhook-url 参数）
    ARCH_PR_NUMBER: PR 编号
    ARCH_PR_URL: PR 链接
    ARCH_REPO: 仓库名（owner/repo）
    GITHUB_TOKEN: GitHub Token（用于 PR 评论，可选）

可观测性约束实现：
- 结构化日志：JSON 格式，含 trace_id/module_name/action/duration_ms
- 边界显性化：网络失败抛出明确异常
- 埋点预留：trackEvent('arch_violation_detected', {...})
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 违规事件归档目录
ARCHIVE_DIR = Path("docs/architecture/violation_events")

# Prometheus 指标文件路径
METRICS_FILE = Path("docs/architecture/arch_metrics.prom")


def _track_event(event_name: str, payload: Dict[str, Any]) -> None:
    """埋点预留：事件追踪占位函数

    Args:
        event_name: 事件名（如 arch_violation_detected）
        payload: 事件数据
    """
    # 埋点占位：实际接入时替换为 BusinessMetricsCollector 调用
    logger.info(
        '{"trace_id":"arch_notify","module_name":"notify_arch_violation",'
        '"action":"track_event","event":"%s","payload":%s}',
        event_name, json.dumps(payload, ensure_ascii=False),
    )


def load_report(report_path: str) -> Dict[str, Any]:
    """加载架构规则校验报告

    Args:
        report_path: 报告 JSON 文件路径

    Returns:
        报告字典

    Raises:
        FileNotFoundError: 报告文件不存在
        json.JSONDecodeError: JSON 解析失败
    """
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"架构校验报告不存在: {report_path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_active_violations(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取未豁免违规列表

    Args:
        report: 校验报告

    Returns:
        未豁免违规列表
    """
    violations = report.get("violations", [])
    return [v for v in violations if not v.get("is_exempted", False)]


def build_event_record(
    report: Dict[str, Any],
    active_violations: List[Dict[str, Any]],
    pr_number: Optional[str],
    pr_url: Optional[str],
    repo: Optional[str],
) -> Dict[str, Any]:
    """构建违规事件记录

    Args:
        report: 校验报告
        active_violations: 未豁免违规列表
        pr_number: PR 编号
        pr_url: PR 链接
        repo: 仓库名

    Returns:
        事件记录字典
    """
    trace_id = report.get("trace_id", uuid.uuid4().hex[:16])
    return {
        "event_id": uuid.uuid4().hex,
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "architecture_violation_detected",
        "repo": repo or "unknown",
        "pr_number": pr_number,
        "pr_url": pr_url,
        "branch": "main",
        "total_violations": report.get("total_violations", 0),
        "active_violations_count": len(active_violations),
        "exempted_violations_count": report.get("exempted_violations", 0),
        "violations": [
            {
                "rule_id": v.get("rule_id", ""),
                "rule_desc": v.get("rule_desc", ""),
                "source": v.get("source", ""),
                "target": v.get("target", ""),
                "source_file": v.get("source_file", ""),
                "line": v.get("line", 0),
                "severity": v.get("severity", "medium"),
                "suggestion": v.get("suggestion", ""),
            }
            for v in active_violations
        ],
        "graph_stats": report.get("graph_stats", {}),
    }


def archive_violation_event(event: Dict[str, Any]) -> Path:
    """归档违规事件到文件

    Args:
        event: 事件记录

    Returns:
        归档文件路径
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = event["timestamp"].replace(":", "-").replace(".", "-")
    filename = f"violation_{timestamp}_{event['event_id'][:8]}.json"
    filepath = ARCHIVE_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False, indent=2)
    logger.info("违规事件已归档: %s", filepath)
    return filepath


def write_prometheus_metrics(
    report: Dict[str, Any],
    active_violations: List[Dict[str, Any]],
) -> Path:
    """写入 Prometheus 指标文件

    指标格式供 node_exporter textfile collector 抓取。

    Args:
        report: 校验报告
        active_violations: 未豁免违规列表

    Returns:
        指标文件路径
    """
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [
        "# HELP yunshu_arch_violations_total 架构违规总数（按豁免状态分类）",
        "# TYPE yunshu_arch_violations_total gauge",
        f'yunshu_arch_violations_total{{status="active"}} {len(active_violations)}',
        f'yunshu_arch_violations_total{{status="exempted"}} {report.get("exempted_violations", 0)}',
        "",
        "# HELP yunshu_arch_rules_total 架构规则总数",
        "# TYPE yunshu_arch_rules_total gauge",
        f'yunshu_arch_rules_total {report.get("total_rules", 0)}',
        "",
        "# HELP yunshu_arch_check_duration_ms 架构校验耗时（毫秒）",
        "# TYPE yunshu_arch_check_duration_ms gauge",
        f'yunshu_arch_check_duration_ms {report.get("duration_ms", 0):.2f}',
        "",
        "# HELP yunshu_arch_violation_by_rule 按规则分类的违规数",
        "# TYPE yunshu_arch_violation_by_rule gauge",
    ]

    # 按规则统计违规
    rule_counts: Dict[str, int] = {}
    for v in active_violations:
        rule_id = v.get("rule_id", "unknown")
        rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1
    for rule_id, count in rule_counts.items():
        lines.append(f'yunshu_arch_violation_by_rule{{rule_id="{rule_id}"}} {count}')

    # 按严重度统计
    lines.append("")
    lines.append("# HELP yunshu_arch_violation_by_severity 按严重度分类的违规数")
    lines.append("# TYPE yunshu_arch_violation_by_severity gauge")
    severity_counts: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for v in active_violations:
        sev = v.get("severity", "medium")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    for sev, count in severity_counts.items():
        lines.append(f'yunshu_arch_violation_by_severity{{severity="{sev}"}} {count}')

    lines.append("")
    with open(METRICS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Prometheus 指标已写入: %s", METRICS_FILE)
    return METRICS_FILE


def send_webhook_notification(
    event: Dict[str, Any],
    webhook_url: str,
    webhook_type: str = "auto",
) -> bool:
    """发送 Webhook 通知

    支持 Slack、Microsoft Teams 和通用 JSON Webhook。

    Args:
        event: 违规事件记录
        webhook_url: Webhook 地址
        webhook_type: Webhook 类型（slack/teams/auto）

    Returns:
        True=发送成功，False=失败
    """
    start = time.perf_counter()

    # 自动检测类型
    if webhook_type == "auto":
        if "hooks.slack.com" in webhook_url:
            webhook_type = "slack"
        elif "webhook.office.com" in webhook_url or "hooks.microsoft.com" in webhook_url:
            webhook_type = "teams"
        else:
            webhook_type = "generic"

    # 构建消息体
    if webhook_type == "slack":
        payload = _build_slack_message(event)
    elif webhook_type == "teams":
        payload = _build_teams_message(event)
    else:
        payload = event

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                '{"trace_id":"%s","module_name":"notify_arch_violation",'
                '"action":"webhook_sent","type":"%s","status":"%d",'
                '"duration_ms":%.2f,"success":true}',
                event["trace_id"], webhook_type, resp.status, elapsed_ms,
            )
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error(
            '{"trace_id":"%s","module_name":"notify_arch_violation",'
            '"action":"webhook_error","type":"%s","error":"%s",'
            '"duration_ms":%.2f,"success":false}',
            event["trace_id"], webhook_type, str(e)[:200], elapsed_ms,
        )
        # 边界显性化：网络失败抛出明确异常而非静默返回
        raise RuntimeError(
            f"Webhook 通知发送失败 ({webhook_type}): {e}"
        ) from e


def _build_slack_message(event: Dict[str, Any]) -> Dict[str, Any]:
    """构建 Slack 消息体"""
    violations = event.get("violations", [])
    violation_text = "\n".join(
        f"• `{v['rule_id']}`: `{v['source']}` → `{v['target']}` "
        f"({v['source_file']}:{v['line']})"
        for v in violations[:10]  # 最多显示 10 条
    )

    pr_info = ""
    if event.get("pr_url"):
        pr_info = f"\n🔗 PR: <{event['pr_url']}|#{event.get('pr_number', '?')}>"

    return {
        "text": f"🚨 架构违规检测 — {event.get('repo', 'unknown')}",
        "attachments": [
            {
                "color": "danger",
                "fields": [
                    {
                        "title": "违规数量",
                        "value": f"{event['active_violations_count']} 个未豁免 "
                        f"({event.get('total_violations', 0)} 总计)",
                        "short": True,
                    },
                    {
                        "title": "Trace ID",
                        "value": event["trace_id"],
                        "short": True,
                    },
                    {"title": "违规详情", "value": violation_text, "short": False},
                ],
                "footer": f"时间: {event['timestamp']}{pr_info}",
            }
        ],
    }


def _build_teams_message(event: Dict[str, Any]) -> Dict[str, Any]:
    """构建 Microsoft Teams 消息体"""
    violations = event.get("violations", [])
    violation_text = "<br>".join(
        f"• <b>{v['rule_id']}</b>: {v['source']} → {v['target']} "
        f"({v['source_file']}:{v['line']})"
        for v in violations[:10]
    )

    pr_link = ""
    if event.get("pr_url"):
        pr_link = f"<br><a href=\"{event['pr_url']}\">PR #{event.get('pr_number', '?')}</a>"

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FF0000",
        "summary": f"架构违规检测 — {event.get('repo', 'unknown')}",
        "sections": [
            {
                "activityTitle": f"🚨 架构违规检测 — {event.get('repo', 'unknown')}",
                "facts": [
                    {"name": "违规数量", "value": f"{event['active_violations_count']} 个未豁免"},
                    {"name": "Trace ID", "value": event["trace_id"]},
                ],
                "text": f"<b>违规详情:</b><br>{violation_text}{pr_link}",
            }
        ],
    }


def comment_on_pr(
    event: Dict[str, Any],
    repo: str,
    pr_number: str,
    token: str,
) -> bool:
    """在 PR 上评论违规详情

    Args:
        event: 违规事件记录
        repo: 仓库名（owner/repo）
        pr_number: PR 编号
        token: GitHub Token

    Returns:
        True=成功
    """
    violations = event.get("violations", [])
    violation_lines = "\n".join(
        f"| `{v['rule_id']}` | `{v['source']}` → `{v['target']}` | "
        f"{v['source_file']}:{v['line']} | {v.get('severity', 'medium')} |"
        for v in violations
    )

    body = (
        f"## 🚨 架构违规告警\n\n"
        f"**Trace ID**: `{event['trace_id']}`\n"
        f"**检测时间**: {event['timestamp']}\n"
        f"**未豁免违规**: {event['active_violations_count']} 个\n\n"
        f"| 规则 | 违规路径 | 文件:行号 | 严重度 |\n"
        f"|------|----------|-----------|--------|\n"
        f"{violation_lines}\n\n"
        f"### 修复建议\n\n"
        + "\n".join(
            f"- **{v['rule_id']}**: {v.get('suggestion', '请查看文档')}"
            for v in violations
        )
        + f"\n\n> ⚠️ 请修复以上违规或登记豁免后再合并。"
    )

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    req = urllib.request.Request(
        url,
        data=json.dumps({"body": body}).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("PR 评论已发送: %s", resp.status)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        logger.error("PR 评论发送失败: %s", e)
        raise RuntimeError(f"PR 评论发送失败: {e}") from e


def main() -> int:
    parser = argparse.ArgumentParser(
        description="架构违规自动通知与监控记录"
    )
    parser.add_argument(
        "--report",
        default="docs/architecture/arch_rules_report.json",
        help="架构校验报告 JSON 路径",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("ARCH_WEBHOOK_URL"),
        help="Webhook 通知地址（Slack/Teams/自定义）",
    )
    parser.add_argument(
        "--webhook-type",
        default="auto",
        choices=["auto", "slack", "teams", "generic"],
        help="Webhook 类型",
    )
    parser.add_argument(
        "--pr-number",
        default=os.environ.get("ARCH_PR_NUMBER"),
        help="PR 编号",
    )
    parser.add_argument(
        "--pr-url",
        default=os.environ.get("ARCH_PR_URL"),
        help="PR 链接",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("ARCH_REPO"),
        help="仓库名（owner/repo）",
    )
    parser.add_argument(
        "--no-pr-comment",
        action="store_true",
        help="跳过 PR 评论",
    )
    parser.add_argument(
        "--no-webhook",
        action="store_true",
        help="跳过 Webhook 通知",
    )
    args = parser.parse_args()

    # 配置日志：同时输出到控制台和文件
    log_file = Path("docs/architecture/notify_arch_violation.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    # 文件 handler（持久化结构化日志，供审计）
    file_handler = logging.FileHandler(str(log_file), mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    total_start = time.perf_counter()

    # 1. 加载报告
    try:
        report = load_report(args.report)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"❌ 加载报告失败: {e}")
        return 1

    # 2. 检查是否有未豁免违规
    active_violations = extract_active_violations(report)

    if not active_violations:
        elapsed_ms = (time.perf_counter() - total_start) * 1000
        logger.info(
            '{"trace_id":"%s","module_name":"notify_arch_violation",'
            '"action":"no_violations","active_count":0,"duration_ms":%.2f}',
            report.get("trace_id", "unknown"), elapsed_ms,
        )
        print("✅ 无未豁免违规，无需发送通知")
        # 仍然更新 Prometheus 指标（active=0）
        write_prometheus_metrics(report, [])
        return 0

    print(f"🚨 检测到 {len(active_violations)} 个未豁免架构违规")

    # 3. 构建事件记录
    event = build_event_record(
        report, active_violations, args.pr_number, args.pr_url, args.repo
    )

    # 4. 埋点：记录违规检测事件
    _track_event("arch_violation_detected", {
        "active_count": len(active_violations),
        "trace_id": event["trace_id"],
        "pr_number": args.pr_number,
    })

    # 5. 归档违规事件
    archive_path = archive_violation_event(event)
    print(f"📁 违规事件已归档: {archive_path}")

    # 6. 写入 Prometheus 指标
    metrics_path = write_prometheus_metrics(report, active_violations)
    print(f"📊 Prometheus 指标已写入: {metrics_path}")

    # 7. 发送 Webhook 通知
    webhook_status = "未配置"
    if args.webhook_url and not args.no_webhook:
        try:
            send_webhook_notification(event, args.webhook_url, args.webhook_type)
            print(f"✅ Webhook 通知已发送 ({args.webhook_type})")
            webhook_status = "已发送"
        except RuntimeError as e:
            print(f"⚠️ Webhook 通知发送失败: {e}")
            webhook_status = "发送失败（已降级）"
            # 不阻断流程，继续后续步骤

    # 8. PR 评论
    pr_comment_status = "未配置"
    if args.pr_number and args.repo and not args.no_pr_comment:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            try:
                comment_on_pr(event, args.repo, args.pr_number, token)
                print(f"✅ PR 评论已发送: #{args.pr_number}")
                pr_comment_status = "已发送"
            except RuntimeError as e:
                print(f"⚠️ PR 评论发送失败: {e}")
                pr_comment_status = "发送失败（已降级）"
        else:
            logger.warning("GITHUB_TOKEN 未设置，跳过 PR 评论")
            pr_comment_status = "跳过（未设置 Token）"

    # 9. 输出汇总
    elapsed_ms = (time.perf_counter() - total_start) * 1000
    logger.info(
        '{"trace_id":"%s","module_name":"notify_arch_violation",'
        '"action":"complete","active_violations":%d,"duration_ms":%.2f}',
        event["trace_id"], len(active_violations), elapsed_ms,
    )
    print(f"\n📋 通知汇总:")
    print(f"  • 违规数: {len(active_violations)} 个未豁免")
    print(f"  • 归档: {archive_path}")
    print(f"  • 指标: {metrics_path}")
    print(f"  • Webhook: {webhook_status}")
    print(f"  • PR评论: {pr_comment_status}")
    print(f"  • 耗时: {elapsed_ms:.0f} ms")

    return 0


if __name__ == "__main__":
    sys.exit(main())
