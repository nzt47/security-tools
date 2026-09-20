#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站点审计 —— 常驻治理工具（TASK-07 第 1 步第 1 项 / E2）

## 它回答的问题

    「`agent/` 里有**多少**地方能发起网络出站？其中哪些**没有**经过统一收口
      （`agent/web/http_client.py`）、哪些**没有**做目标地址判定？」

## 用法

    python scripts/audit_egress_points.py              # 人读报告（有未登记项 ⇒ 退出码 1）
    python scripts/audit_egress_points.py --json       # 机器可读（CI 消费）
    python scripts/audit_egress_points.py --strict     # 连"LLM 可达的例外"也算失败

## 分类口径（四类，互不重叠）

    chokepoint  经 `agent.web.http_client`（SSRF 守卫 + 策略守卫 + 重定向复检全在此处）
    guard       守卫自身（`guardrails/ssrf_guard.py`）
    exempt      **显式登记的例外**：每条必须写清 ①目标来源 ②是否 LLM 可达 ③理由
    violation   未登记 —— **本工具存在的意义就是让这一类为零**

## 为什么要有这张白名单表（而不是"全都要改"）

    仓库里有一批**基建性**直连：告警通道（Loki / 告警通知）、扩展市场下载、
    代理自检。把它们全部改走 `HttpClient` 有两个真实代价：
      · 告警通道改走守卫会形成"拦截导致告警失联"的循环依赖（TASK-07 §3 第 1 步
        第 2 项明确允许 `alert_notifier` 例外）；
      · 扩展安装/市场下载是**运维动作**，目标来自配置而非 LLM 参数。
    故本表**逐条**登记并写明理由，而不是用一句"这些是内部的"整体放行。
    **`llm_reachable: True` 的例外是本表的重点审查对象**：它们是"LLM 能触达但
    未经统一收口"的残余面，`--strict` 会因此失败（供 CI 逐步收敛）。

## 不易 / 变易 / 简易

    不易：判据是 AST 而不是正则 —— 正则会把注释、字符串里的 `requests.get` 也算进来
          （本仓注释里大量出现这些词），产出假阳性。
    变易：新增出站原语只需往 `EGRESS_VERBS` / `EGRESS_MODULES` 加一条。
    简易：纯标准库；只读，不修改任何文件；不发起任何网络请求。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 出站函数名（`模块.动词` 形态的第二个元素）
EGRESS_VERBS = frozenset({
    "get", "post", "put", "delete", "head", "patch", "options", "request",
    "Session", "urlopen", "urlretrieve", "Request", "ClientSession",
})

#: 触发"这是出站"的模块前缀
EGRESS_MODULES = ("requests", "httpx", "aiohttp", "urllib", "urllib3", "selenium")

#: 统一收口所在的文件（其余文件里的出站调用默认视为需要解释）
CHOKEPOINT_FILES = ("agent/web/http_client.py",)

#: 守卫自身（它们当然会碰网络原语）
GUARD_FILES = ("agent/guardrails/ssrf_guard.py",)

#: ════════════════════════════════════════════════════════════
#:  显式例外表（每条必须给出 `target_source` / `llm_reachable` / `reason`）
#: ════════════════════════════════════════════════════════════
#: 纪律（TASK-07 §3 第 1 步第 2 项）：例外**必须**在代码里也是显式标注的。
#: `target_source` 取值：
#:     config  目标来自配置文件（运维可管、LLM 改不了）
#:     hardcoded 目标硬编码在代码里
#:     runtime 目标来自运行期上下文（如自检时的代理地址）
#:     llm     **目标可能来自 LLM 参数**（最高风险，`--strict` 下失败）
EXEMPT_SITES: Dict[str, Dict[str, Any]] = {
    "agent/monitoring/alert_notifier.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("**有意例外**（TASK-07 §3 第 1 步第 2 项明确允许）：告警通道若改走 "
                   "HttpClient，会形成「SSRF 拦截把告警一起拦掉」的循环依赖 —— "
                   "安全事件发生时反而发不出告警。目标地址全部来自配置。"),
    },
    "agent/monitoring/loki.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("日志推送（Loki push）。目标来自 `observability_config`，"
                   "且日志通道与告警同属「被拦截会造成可观测性黑洞」的一类。"),
    },
    "agent/monitoring/search.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("搜索引擎健康自检。**查询词**由 LLM 控制，但**目标 URL**来自引擎配置表，"
                   "不构成 SSRF（攻击者无法指定主机）。待办：收敛到 HttpClient 后"
                   "可同时获得 SSRF 与策略两层守卫。"),
    },
    "agent/monitoring/trace_http_client.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("trace 上报端点（观测后端）。目标来自配置。"),
    },
    "agent/monitoring/error_reporter.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": "错误上报端点（观测后端）。目标来自配置。",
    },
    "agent/cognitive/logging_integration.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": "Loki 推送（同 `monitoring/loki.py` 的通道）。目标来自配置。",
    },
    "agent/extensions/installer.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("扩展包下载（`urllib.request.urlretrieve`）。目标来自扩展清单/市场配置，"
                   "属**运维动作**；`ext_install` 在治理平面属 `effect=extend`，"
                   "已受审批边界约束（`tool_gate`）。"),
    },
    "agent/extensions/channels_installer.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": "渠道包下载，同 `extensions/installer.py`。",
    },
    "agent/extensions/market.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": "扩展市场索引拉取。目标来自市场配置（运维可管）。",
    },
    "agent/system_tools.py": {
        "target_source": "hardcoded",
        "llm_reachable": False,
        "reason": ("`get_weather` 固定访问 `wttr.in`（**硬编码**，无参数可注入）。"
                   "待办：收敛到 HttpClient 以获得统一超时/重试。"),
    },
    "agent/tools/mcp_connector.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("MCP 连接（JSON-RPC over HTTP）。目标来自 `network_config` 的 MCP 服务清单。"
                   "**TASK-07 已在两处补上出站判定**：①写入配置时 "
                   "`agent/network_config.py::validate_mcp_service`；"
                   "②**每次调用**都在 `_ssrf_block_reason()` 里复检（`location=remote` 的 "
                   "MCP handler 参数由 LLM 给，属真实出站路径）。"
                   "保留裸 `urllib` 直连是因为 MCP 的 JSON-RPC over HTTP 语义"
                   "（`tools/list` / `tools/call`）与 `HttpClient` 的网页请求契约不同，"
                   "**判定口径仍复用同一个 `ssrf_guard.check_url`**（D1）。"),
    },
    "agent/server_routes/routes_config.py": {
        "target_source": "runtime",
        "llm_reachable": False,
        "reason": ("配置页的「测试连接」按钮：目标是**人在 UI 里填的地址**，"
                   "属运维显式动作（且受 UI 鉴权与审计约束）。"
                   "待办：该动作也应过出站判定，登记为 TASK-07 遗留。"),
    },
    "agent/server_routes/routes_logging.py": {
        "target_source": "runtime",
        "llm_reachable": False,
        "reason": "日志后端「测试连接」，同 `routes_config.py`。",
    },
    "agent/skills_mgmt/creator.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": "技能模板拉取。目标来自技能仓库配置。",
    },
    "agent/web/crawler_control.py": {
        "target_source": "runtime",
        "llm_reachable": False,
        "reason": ("代理自检（`test_proxy`）：目标由**运维配置的代理地址**推导，"
                   "`requests.get(test_url)` 的 `test_url` 不是 LLM 参数。"
                   "待办：爬虫主链路（若将来接回 LLM 可达的工具）必须走 HttpClient。"),
    },
    "agent/capregistry/loader.py": {
        "target_source": "config",
        "llm_reachable": False,
        "reason": ("远端能力的 HTTP/SSE Loader。**非流式分支走 `HttpClient.request`**"
                   "（两层守卫齐全）；流式分支因 `stream=True` 拿不到响应对象而"
                   "自建请求，**已分别复现 SSRF 与策略两层判定**"
                   "（见 `SseLoader._raw_request`，TASK-07 补齐）。"
                   "此处的裸 `requests.request` 是「HttpClient 未暴露 session」的极端兜底，"
                   "正常路径取的是 `client._session`。"),
    },
}


class _Visitor(ast.NodeVisitor):
    """收集出站调用点（AST 口径，不做正则匹配）"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.sites: List[Tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802  AST 约定名
        name = _dotted_name(node.func)
        if name and _is_egress_primitive(name):
            self.sites.append((int(getattr(node, "lineno", 0)), name))
        self.generic_visit(node)


def _dotted_name(node: Any) -> str:
    """把 `a.b.c` 形态的 Attribute/Name 还原成点号字符串；否则空串"""
    parts: List[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _is_egress_primitive(name: str) -> bool:
    parts = name.split(".")
    if not parts:
        return False
    # `self._session.request(...)` 这类"持有 requests.Session 再调用"的形态：
    # 组件名不是模块名，但**确实是出站调用**（`HttpClient` 的主路径就是它）。
    # 不认这一条会把统一收口本身的调用点漏报成 0（本工具第一版实测漏了）。
    # 【判据必须收紧到**恰好** `session` / `_session` 这个组件名】第一版写成
    # "组件名里含 session"，于是把 `IDENTITY_SESSION_SOURCE.get`、`self._sessions.get`
    # （普通 dict 查找）全报成出站点 —— 产出 15 条假阳性。**假阳性会让人开始忽略这份
    # 报告**，那比漏报更糟；故这里只认单数、精确匹配的会话组件名。
    if len(parts) >= 2 and parts[-2] in ("_session", "session") \
            and parts[-1] in EGRESS_VERBS:
        return True
    if parts[0] not in EGRESS_MODULES:
        return False
    if parts[0] == "urllib3":
        # urllib3 只认连接池级别；本仓不直接用
        return "PoolManager" in parts or "connection_from" in parts
    return parts[-1] in EGRESS_VERBS


def scan(*, root: str = "") -> Dict[str, Any]:
    """扫描 `agent/` 下的全部出站点并分类"""
    base = root or REPO_ROOT
    package = os.path.join(base, "agent")
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(package):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        for filename in filenames:
            if filename.endswith(".py"):
                files.append(os.path.join(dirpath, filename))

    records: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    for path in sorted(files):
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        visitor = _Visitor(rel)
        visitor.visit(tree)
        if not visitor.sites:
            continue
        exempt = EXEMPT_SITES.get(rel)
        if rel in CHOKEPOINT_FILES:
            kind, reason = "chokepoint", "统一出站收口（SSRF + 策略 + 重定向复检）"
        elif rel in GUARD_FILES:
            kind, reason = "guard", "守卫自身的连接期校验"
        elif exempt is not None:
            kind, reason = "exempt", str(exempt.get("reason") or "")
        else:
            kind, reason = "violation", "未登记的出站点（必须登记理由或收敛到 HttpClient）"
        for lineno, call in visitor.sites:
            record = {
                "file": rel, "line": lineno, "call": call, "kind": kind,
                "reason": reason,
                "target_source": (exempt or {}).get("target_source", ""),
                "llm_reachable": bool((exempt or {}).get("llm_reachable", False)),
            }
            records.append(record)
            if kind == "violation":
                violations.append(record)

    by_kind: Dict[str, int] = {}
    for record in records:
        by_kind[record["kind"]] = by_kind.get(record["kind"], 0) + 1
    strict_violations = [r for r in records
                         if r["kind"] == "exempt" and r["llm_reachable"]]
    return {
        "schema": "egress_points.v1",
        "files_scanned": len(files),
        "sites_total": len(records),
        "by_kind": by_kind,
        "sites": records,
        "violations": violations,
        "llm_reachable_exempt": strict_violations,
    }


def render(report: Dict[str, Any], *, strict: bool = False) -> str:
    lines: List[str] = []
    lines.append("═" * 78)
    lines.append("出站点审计（TASK-07 E2）")
    lines.append("═" * 78)
    lines.append(f"扫描文件: {report['files_scanned']}   出站点: {report['sites_total']}")
    lines.append("")
    lines.append("| 分类 | 数量 | 含义 |")
    lines.append("|---|---|---|")
    labels = {
        "chokepoint": "经 agent.web.http_client（统一收口）",
        "guard": "守卫自身",
        "exempt": "已登记例外（每条有理由）",
        "violation": "**未登记（必须为零）**",
    }
    for kind in ("chokepoint", "guard", "exempt", "violation"):
        lines.append(f"| `{kind}` | {report['by_kind'].get(kind, 0)} | "
                     f"{labels[kind]} |")
    lines.append("")
    lines.append("## 已登记例外（逐条理由）")
    lines.append("")
    lines.append("| 文件 | 目标来源 | LLM 可达 | 理由 |")
    lines.append("|---|---|---|---|")
    seen: set = set()
    for record in report["sites"]:
        if record["kind"] != "exempt" or record["file"] in seen:
            continue
        seen.add(record["file"])
        reason = str(record["reason"]).replace("|", "\\|")
        lines.append(f"| `{record['file']}` | {record['target_source']} | "
                     f"{'⚠️ 是' if record['llm_reachable'] else '否'} | {reason} |")
    lines.append("")
    if report["violations"]:
        lines.append("## 🔴 未登记出站点（必须处置）")
        lines.append("")
        for record in report["violations"]:
            lines.append(f"  - {record['file']}:{record['line']}  `{record['call']}`")
        lines.append("")
    if strict and report["llm_reachable_exempt"]:
        lines.append("## 🔴 --strict：LLM 可达的例外（需收敛到 HttpClient）")
        lines.append("")
        for record in report["llm_reachable_exempt"]:
            lines.append(f"  - {record['file']}:{record['line']}  `{record['call']}`")
        lines.append("")
    ok = not report["violations"] and not (strict and report["llm_reachable_exempt"])
    lines.append("[OK] 白名单外的直连为 0" if ok else "[FAIL] 存在需要处置的出站点")
    return "\n".join(lines)


def _force_utf8_stdio() -> None:
    """把 stdio 换成 UTF-8（本沙箱是 UTF-8/GBK 混合环境，见 TASK-00 §0.2d 第 1 类）"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="出站点审计（TASK-07 E2）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict", action="store_true",
                        help="连 LLM 可达的例外也算失败（CI 逐步收敛用）")
    parser.add_argument("--root", default="", help="仓库根（缺省自动推导）")
    args = parser.parse_args(argv)

    report = scan(root=args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report, strict=args.strict))
    if report["violations"]:
        return 1
    if args.strict and report["llm_reachable_exempt"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
