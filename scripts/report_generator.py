#!/usr/bin/env python3
"""通用报告生成器: 将结构化检查结果(items)规范化为统一报告, 支持多格式导出

从 verify_core_invariants.py 的 --json 输出逻辑提炼为通用能力:
任何"逐项检查/校验"类工具可复用, 统一产出:

  - dict(report):  规范化结构 {tool, status, generated_at, meta, total, blocked, items}
  - JSON:          to_json(report), stdout 友好(CI 消费)
  - 文本:          to_text(report), 人类可读
  - HTML:          to_html(report), 自包含单文件(内联 CSS, 浏览器直开)

items 元素约定(不变量):
  id / path / desc / status("pass"|"BLOCK") / detail —— 缺失字段容忍(空串兜底)

用法(作为库):
    import report_generator as rg
    report = rg.build_report(tool="my_tool", items=[...], meta={...})
    text = rg.to_text(report)
    html  = rg.to_html(report)
    js    = rg.to_json(report)
"""
from __future__ import annotations

import html as _html
import json
import time
from typing import Any

# status 归一化: 任何非 "pass" 值统一显示为 BLOCK(阻止语义)
_PASS_VALUES = {"pass", "passed", "ok", "true", "PASS"}


def _norm_status(status: Any) -> str:
    return "pass" if str(status).lower() in {s.lower() for s in _PASS_VALUES} else "BLOCK"


def build_report(
    *,
    tool: str,
    items: list[dict],
    meta: dict | None = None,
) -> dict:
    """规范化报告结构

    Args:
        tool: 工具/报告名称(如 verify_core_invariants)
        items: 逐项检查结果 [{id,path,desc,status,detail}]
        meta: 附加元数据(如 repo_root/commit 等)

    Returns:
        dict: 统一报告
    """
    norm_items = []
    for it in items:
        norm_items.append({
            "id": str(it.get("id", "")),
            "path": str(it.get("path", "")),
            "desc": str(it.get("desc", "")),
            "status": _norm_status(it.get("status")),
            "detail": str(it.get("detail", "")),
        })
    blocked = [i for i in norm_items if i["status"] == "BLOCK"]
    return {
        "tool": tool,
        "status": "fail" if blocked else "pass",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta or {},
        "total": len(norm_items),
        "blocked": len(blocked),
        "items": norm_items,
    }


def to_json(report: dict, indent: int | None = 2) -> str:
    """JSON 序列化(stdout 友好: 默认 indent=2, 单文档)"""
    return json.dumps(report, ensure_ascii=False, indent=indent)


def to_text(report: dict) -> str:
    """人类可读文本摘要(不含逐项明细, 明细由调用方自行打印)"""
    status = "PASS" if report["status"] == "pass" else "FAIL"
    return (f"[{report['tool']}] {status}: "
            f"{report['total'] - report['blocked']}/{report['total']} 项通过, "
            f"{report['blocked']} 项被破坏 → "
            f"exit {0 if report['status'] == 'pass' else 1}")


def to_html(report: dict) -> str:
    """自包含 HTML 报告(内联 CSS, 无外部依赖, 可直接浏览器打开/归档)"""
    tool = _html.escape(report["tool"])
    overall = "PASS" if report["status"] == "pass" else "FAIL"
    cls = "overall-pass" if report["status"] == "pass" else "overall-fail"
    rows = []
    for i in report["items"]:
        row_cls = "pass" if i["status"] == "pass" else "fail"
        rows.append(
            f"<tr class=\"{row_cls}\">"
            f"<td>{_html.escape(i['id'])}</td>"
            f"<td><code>{_html.escape(i['path'])}</code></td>"
            f"<td>{_html.escape(i['desc'])}</td>"
            f"<td>{_html.escape(i['status'])}</td>"
            f"<td>{_html.escape(i['detail'])}</td>"
            f"</tr>"
        )
    meta_html = "".join(
        f"<li><code>{_html.escape(k)}</code>: {_html.escape(str(v))}</li>"
        for k, v in report["meta"].items()
    ) or "<li>无</li>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{tool} 报告</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         margin: 24px; color: #24292f; }}
  h1 {{ font-size: 20px; }}
  .overall-pass {{ color: #1a7f37; font-weight: 700; }}
  .overall-fail {{ color: #cf222e; font-weight: 700; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left;
           font-size: 13px; }}
  th {{ background: #f6f8fa; }}
  tr.pass td {{ background: #f0fff4; }}
  tr.fail td {{ background: #fff5f5; }}
  code {{ background: #f6f8fa; padding: 1px 4px; border-radius: 4px; }}
  .meta {{ color: #57606a; font-size: 12px; }}
</style>
</head>
<body>
<h1>{tool} 报告</h1>
<p class="meta">生成时间: {_html.escape(report['generated_at'])}</p>
<p>总体: <span class="{cls}">{overall}</span>
   (通过 {report['total'] - report['blocked']}/{report['total']},
    破坏 {report['blocked']})</p>
<h2>元数据</h2>
<ul class="meta">{meta_html}</ul>
<h2>逐项结果</h2>
<table>
  <tr><th>ID</th><th>文件</th><th>校验内容</th><th>状态</th><th>详情</th></tr>
  {''.join(rows)}
</table>
</body>
</html>
"""
