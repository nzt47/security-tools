#!/usr/bin/env python3
"""Slack Incoming Webhook 通知脚本(CI 失败场景自动触发)

背景: core-invariants-guard 工作流检测到核心不变量被破坏时 job 失败,
本脚本在其失败路径上发送 Slack 通知, 便于人工介入修复。

设计(三义):
- 不易: Webhook URL 绝不硬编码——仅从环境变量 SLACK_WEBHOOK_URL 或
        --webhook-url 读取; --json 模式 stdout 仅 JSON(CI 消费约定)
- 变易: payload 可定制(--title/--text/--color/--fields); 支持直接读取
        verify_core_invariants.py 的 JSON 报告(--json-file)自动提取被破坏项
- 简易: 零第三方依赖(仅标准库 urllib); 单文件; 重试优先复用统一
        RetryPolicy(agent 包可用时, 守硬约束), CI 环境无 agent 包时
        回退极简固定重试(跨仓库/CI 独立安全)

用法:
    # 失败场景通知(推荐: 直接读 verify_core_invariants 报告)
    python scripts/slack_notify.py --json-file invariant_report.json \
        --title "core-invariants-guard 失败" \
        --repo "owner/repo" \
        --run-url "https://github.com/owner/repo/actions/runs/123"

    # 自定义文本
    python scripts/slack_notify.py --title "CI 失败" --text "细节..."

    # 本地模拟(不真正发送)
    python scripts/slack_notify.py --dry-run --title "CI 失败" --text "..."

退出码: 0=发送成功(或 dry-run); 1=发送失败; 2=参数/配置错误(缺 webhook)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

# ═══ HTTP 状态可重试集合(与极简重试/RetryPolicy 判定共用) ═══
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


class _HttpStatusError(Exception):
    """HTTP 非 2xx 响应包装为异常, 供统一重试判定"""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def _is_retryable(err: Exception) -> bool:
    """极简重试判定(CI 环境无 agent 包时使用): 网络错误 / 429 / 5xx"""
    if isinstance(err, _HttpStatusError):
        return err.status in _RETRYABLE_STATUS
    return isinstance(err, (OSError, TimeoutError))


def _make_policy(max_retries: int, retry_delay: float):
    """优先使用统一 RetryPolicy(守硬约束); 失败(CI 无 agent 包)返回 None

    RetryPolicy.__init__ 在 max_retries=None 时会 import agent.monitoring
    .observability_config, 因此此处必须显式传参, 保证无 agent 包环境不炸。
    """
    try:
        from agent.error_handler import RetryPolicy
        return RetryPolicy(
            max_retries=max_retries,
            initial_delay=retry_delay,
            strategy="fixed",
            retryable_exceptions=(OSError, TimeoutError, _HttpStatusError),
            custom_retry_condition=lambda e: (
                isinstance(e, _HttpStatusError) and e.status in _RETRYABLE_STATUS
            ) or isinstance(e, (OSError, TimeoutError)),
        )
    except ImportError:
        return None


def _post_once(url: str, payload: dict) -> tuple[int, str]:
    """发送一次 webhook 请求: 返回 (http_status, body)"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace")
        return resp.status, body


def _post_with_retry(
    url: str,
    payload: dict,
    max_retries: int,
    retry_delay: float,
    policy,
) -> dict:
    """发送并重试: 成功 → {ok:True,...}; 最终失败 → {ok:False,...}"""
    attempts = 0
    last_error: str | None = None
    last_status: int | None = None
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            status, body = _post_once(url, payload)
            if 200 <= status < 300:
                return {"ok": True, "http_status": status, "attempts": attempts}
            raise _HttpStatusError(status, body)
        except Exception as err:  # noqa: BLE001 - 统一收集最后一跳错误
            last_error = repr(err)
            last_status = err.status if isinstance(err, _HttpStatusError) else None
            retryable = (
                policy.should_retry(err, attempt)
                if policy is not None
                else _is_retryable(err) and attempt < max_retries
            )
            if not retryable:
                break
            delay = (
                policy.calculate_delay(attempt)
                if policy is not None
                else retry_delay
            )
            time.sleep(delay)
    result = {"ok": False, "attempts": attempts}
    if last_status is not None:
        result["http_status"] = last_status
    if last_error:
        result["error"] = last_error
    return result


def _extract_from_report(path: str) -> tuple[str, dict]:
    """从 verify_core_invariants 报告 JSON 提取: (通知正文, 附加 fields)"""
    with open(path, "r", encoding="utf-8") as f:
        report = json.load(f)
    blocked = [i for i in report.get("items", []) if i.get("status") == "BLOCK"]
    lines = [f"{report.get('blocked', len(blocked))}/{report.get('total', 0)} 项不变量被破坏"]
    for b in blocked:
        lines.append(f"• [{b.get('id')}] {b.get('path')}: {b.get('detail')}")
    fields = {
        "total": str(report.get("total", "")),
        "blocked": str(report.get("blocked", "")),
        "overall_status": str(report.get("status", "")),
    }
    return "\n".join(lines), fields


def _build_payload(args) -> dict:
    """构建 Slack Incoming Webhook payload"""
    text = args.text or ""
    fields: list[dict] = []
    if args.json_file:
        extracted, report_fields = _extract_from_report(args.json_file)
        text = extracted or text
        fields.append({
            "title": "不变量校验",
            "value": " | ".join(f"{k}={v}" for k, v in report_fields.items()),
            "short": True,
        })
    if args.fields:
        for k, v in json.loads(args.fields).items():
            fields.append({"title": str(k), "value": str(v), "short": True})
    if args.repo:
        fields.append({"title": "仓库", "value": args.repo, "short": True})
    if args.run_url:
        fields.append({"title": "Actions 运行", "value": args.run_url, "short": False})
    attachment = {"color": args.color, "title": args.title, "text": text}
    if fields:
        attachment["fields"] = fields
    return {"text": f":red_circle: {args.title}", "attachments": [attachment]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--webhook-url", default=os.environ.get("SLACK_WEBHOOK_URL", ""),
                   help="Slack Incoming Webhook URL(默认取环境变量 SLACK_WEBHOOK_URL)")
    p.add_argument("--title", default="CI 失败通知", help="通知标题")
    p.add_argument("--text", default="", help="通知正文(与 --json-file 二选一)")
    p.add_argument("--json-file", default="",
                   help="读取 verify_core_invariants 报告 JSON, 自动提取被破坏项")
    p.add_argument("--color", default="danger", help="attachment 颜色(默认 danger)")
    p.add_argument("--fields", default="",
                   help="额外字段 JSON dict, 如 '{\"commit\":\"abc\"}'")
    p.add_argument("--repo", default="", help="仓库标识(如 owner/repo)")
    p.add_argument("--run-url", default="", help="GitHub Actions run URL")
    p.add_argument("--retries", type=int, default=1, help="失败重试次数(默认 1)")
    p.add_argument("--retry-delay", type=float, default=2.0, help="重试间隔秒(默认 2.0)")
    p.add_argument("--json", action="store_true", help="stdout 仅 JSON(CI 消费)")
    p.add_argument("--dry-run", action="store_true", help="模拟发送, 不真正请求")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    # 不易: webhook 必须显式配置, 禁止硬编码
    if not args.webhook_url and not args.dry_run:
        print("::error::SLACK_WEBHOOK_URL 未配置(环境变量或 --webhook-url)", file=sys.stderr)
        return 2

    payload = _build_payload(args)
    if args.dry_run:
        result = {
            "tool": "slack_notify",
            "ok": True,
            "dry_run": True,
            "attempts": 1,
            "title": args.title,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[slack_notify][DRY-RUN] 模拟发送: {args.title}")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    policy = _make_policy(args.retries, args.retry_delay)
    result = _post_with_retry(
        args.webhook_url, payload,
        max_retries=args.retries, retry_delay=args.retry_delay, policy=policy,
    )
    result.setdefault("tool", "slack_notify")
    result.setdefault("title", args.title)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print(f"[slack_notify] 通知发送成功: {args.title} "
              f"(HTTP {result.get('http_status')}, 尝试 {result['attempts']} 次)")
    else:
        print(f"[slack_notify][ERROR] 通知发送失败: {args.title} "
              f"{result.get('error', '')}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
