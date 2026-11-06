# -*- coding: utf-8 -*-
"""把 CodeQL 告警的 **codeFlows（源 → 中间 → sink）** 打出来（2026-11-07）。

【为什么需要】C-6 的 4 条 `py/stack-trace-exposure` 连修两轮都没少：告警挂在**共享 sink**
（`api_envelope.ok()` / `problem()`）上，只要还有**任何一条**源把异常派生串流进去，
sink 的告警就不会消失 —— 在 sink 附近改文案、或盲改猜到的 producer，都是白改。

【怎么取到 codeFlows】默认 CodeQL setup（dynamic）不产出可下载的 SARIF 工件，但**分析结果**
本身能取，只是要显式协商媒体类型（文档里没写，2026-11-07 实测可行）：

    gh api -H "Accept: application/sarif+json" \
        "repos/<owner>/<repo>/code-scanning/analyses/<analysis_id>"

返回的就是完整 SARIF 2.1.0（含 `codeFlows.threadFlows.locations`）。

【用法】

    # 最新一次 python 分析 + 默认规则，打印每条告警的每条流
    python scripts/audit/codeql_flows.py

    # 只看**源**（去重）：定位"还有哪些 producer 要改"
    python scripts/audit/codeql_flows.py --sources-only

    # 换规则 / 换语言 / 换仓库
    python scripts/audit/codeql_flows.py --rule py/clear-text-logging-sensitive-data
    python scripts/audit/codeql_flows.py --language javascript-typescript

产物缓存在 `data/_codeql_sarif_<language>.json`（gitignored 的运行期目录），`--refresh` 强制重取。

【口径】只读；不改仓库、不改告警状态、不 dismiss。凭据由 `gh` 自己管，本脚本不碰 token。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "nzt47/security-tools"
DEFAULT_RULE = "py/stack-trace-exposure"
DEFAULT_LANGUAGE = "python"

#: SARIF 里结果总数上限（默认 setup 全量推送，Python 侧约 90~150 条）
_MAX_RESULTS = 500

#: 每条流的打印上限（步数多时只看头尾，避免刷屏）
_MAX_STEPS = 40


def _gh(args: Iterable[str]) -> str:
    """跑 gh 并返回 stdout（失败把 stderr 抛出来，别静默给空结果）。"""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0:
        raise SystemExit("[codeql_flows] gh 失败（%d）：%s"
                         % (proc.returncode, (proc.stderr or "").strip()[:500]))
    return proc.stdout or ""


def latest_analysis(repo: str, language: str) -> Dict[str, Any]:
    """该语言**最近一次**分析（按 created_at 取最新；列表 API 已按时间倒序）。"""
    out = _gh(["api",
               "repos/%s/code-scanning/analyses?per_page=30" % repo])
    for item in json.loads(out or "[]"):
        if str(item.get("category") or "") == "/language:%s" % language:
            return item
    raise SystemExit("[codeql_flows] 找不到 /language:%s 的分析（语言写错了？）" % language)


def fetch_sarif(repo: str, analysis_id: int) -> Dict[str, Any]:
    out = _gh(["api", "-H", "Accept: application/sarif+json",
               "repos/%s/code-scanning/analyses/%s" % (repo, analysis_id)])
    doc = json.loads(out or "{}")
    if doc.get("version") != "2.1.0":
        raise SystemExit("[codeql_flows] 返回的不是 SARIF 2.1.0（Accept 没生效？）")
    return doc


def _sink_of(result: Dict[str, Any]) -> str:
    loc = ((result.get("locations") or [{}])[0]).get("physicalLocation", {})
    uri = (loc.get("artifactLocation") or {}).get("uri") or "?"
    line = (loc.get("region") or {}).get("startLine")
    return "%s:%s" % (uri, line)


def _flows(result: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """把 codeFlows 摊平成「每条流 = 一组 step location」列表。"""
    out: List[List[Dict[str, Any]]] = []
    for flow in result.get("codeFlows") or []:
        for thread in flow.get("threadFlows") or []:
            steps = [loc.get("location") or {}
                     for loc in (thread.get("locations") or [])]
            if steps:
                out.append(steps)
    return out


def _step_text(step: Dict[str, Any]) -> Tuple[str, str]:
    loc = step.get("physicalLocation") or {}
    uri = (loc.get("artifactLocation") or {}).get("uri") or "?"
    line = (loc.get("region") or {}).get("startLine")
    msg = (step.get("message") or {}).get("text") or ""
    return "%s:%s" % (uri, line), msg


def collect(doc: Dict[str, Any], rule: str) -> List[Tuple[str, List[List[Dict[str, Any]]]]]:
    hits: List[Tuple[str, List[List[Dict[str, Any]]]]] = []
    for run in doc.get("runs") or []:
        for res in (run.get("results") or [])[:_MAX_RESULTS]:
            if res.get("ruleId") != rule:
                continue
            hits.append((_sink_of(res), _flows(res)))
    return hits


def render(results: List[Tuple[str, List[List[Dict[str, Any]]]]]) -> str:
    lines: List[str] = []
    lines.append("结果 %d 条 · 流 %d 条"
                 % (len(results), sum(len(f) for _, f in results)))
    for sink, flows in results:
        lines.append("=" * 72)
        lines.append("SINK: %s（%d 条流）" % (sink, len(flows)))
        for i, steps in enumerate(flows):
            lines.append("  flow#%d 步数 %d" % (i, len(steps)))
            for j, step in enumerate(steps[:_MAX_STEPS]):
                where, msg = _step_text(step)
                lines.append("    %2d) %-46s %s" % (j, where, msg[:80]))
    return "\n".join(lines)


def render_sources(results: List[Tuple[str, List[List[Dict[str, Any]]]]]) -> str:
    """按 sink 汇总**去重的源**（flow 的第一个 step）——「还要改哪几个文件」的清单。"""
    lines: List[str] = []
    for sink, flows in results:
        sources: List[str] = []
        for steps in flows:
            where, _ = _step_text(steps[0])
            if where not in sources:
                sources.append(where)
        lines.append("%s" % sink)
        for where in sources:
            lines.append("    ← %s" % where)
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打印 CodeQL 告警的 codeFlows（源→sink）")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--rule", default=DEFAULT_RULE)
    ap.add_argument("--language", default=DEFAULT_LANGUAGE)
    ap.add_argument("--refresh", action="store_true", help="强制重新拉 SARIF")
    ap.add_argument("--sources-only", action="store_true", help="只打印去重后的源")
    ap.add_argument("--json", action="store_true", help="机器可读输出（sink → 源列表）")
    args = ap.parse_args(argv)

    cache = ROOT / "data" / ("_codeql_sarif_%s.json" % args.language)
    analysis_id = None
    if not cache.exists() or args.refresh:
        analysis = latest_analysis(args.repo, args.language)
        analysis_id = int(analysis.get("id") or 0)
        doc = fetch_sarif(args.repo, analysis_id)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    else:
        doc = json.loads(cache.read_text(encoding="utf-8"))

    results = collect(doc, args.rule)
    if args.json:
        payload = []
        for sink, flows in results:
            payload.append({
                "sink": sink,
                "flows": len(flows),
                "sources": sorted({_step_text(s[0])[0] for s in flows}),
            })
        print(json.dumps({"rule": args.rule, "analysis_id": analysis_id,
                          "sinks": payload}, ensure_ascii=False, indent=2))
    elif args.sources_only:
        print(render_sources(results))
    else:
        print(render(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
