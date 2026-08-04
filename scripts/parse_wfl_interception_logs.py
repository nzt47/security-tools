"""拦截层日志解析报表 — 从日志提取所有被拦截的请求与置信度分数

解析对象: orchestrator 工作流学习层（LAYER_WORKFLOW_LEARNING）的结构化日志。
兼容两种格式:
    1. Python dict repr:   {'module_name': 'orchestrator', 'action': 'orchestrator.wfl.hit', ...}
    2. JSON 行:            {"module_name": "orchestrator", "action": "orchestrator.wfl.hit", ...}

识别 action:
    orchestrator.wfl.hit          拦截命中（含 wf/score/conf/耗时/跳过LLM）
    orchestrator.wfl.miss         未命中（含耗时）
    orchestrator.wfl.exec_failed  命中但执行失败（降级 LLM）
    orchestrator.wfl.error        异常降级 LLM
    orchestrator.wfl.learned      自动学习落库（辅助信息）

输出统计报表（Markdown）:
    - 总体: 拦截总数 / 命中 / 未命中 / 执行失败 / 异常 → 命中率
    - 按工作流: 命中次数 Top-N（含平均 score/conf）
    - score 分桶: <0.25 / 0.25-0.4 / 0.4-0.6 / 0.6-0.8 / >=0.8
    - conf 分桶: <0.4 / 0.4-0.7 / 0.7-0.9 / >=0.9
    - 命中延迟: 均值 / P50 / P95 / P99 / 最大

用法:
    python scripts/parse_wfl_interception_logs.py app.log
    python scripts/parse_wfl_interception_logs.py logs/*.log --top-n 5
    python scripts/parse_wfl_interception_logs.py --demo        # 内置样例验证
    python scripts/parse_wfl_interception_logs.py app.log --json report.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ACTIONS = ("hit", "miss", "exec_failed", "error", "learned")


def _parse_line(line: str):
    """解析一行日志为 dict；无法解析返回 None"""
    line = line.strip()
    if not line:
        return None
    # JSON 行
    if line.startswith("{"):
        try:
            return json.loads(line)
        except (ValueError, TypeError):
            return None
    # Python dict repr: 取第一个 { 到最后一个 }
    start, end = line.find("{"), line.rfind("}")
    if start >= 0 and end > start:
        try:
            return ast.literal_eval(line[start:end + 1])
        except (ValueError, SyntaxError):
            return None
    return None


def _extract(entry: dict) -> dict | None:
    """提取 wfl 层条目; 返回 {action, wf, score, conf, duration_ms, skipped_llm}"""
    action = entry.get("action", "")
    m = re.match(r"orchestrator\.wfl\.(\w+)", action)
    if not m:
        return None
    act = m.group(1)
    if act not in ACTIONS:
        return None
    out = {
        "action": act,
        "wf": entry.get("workflow_id") or entry.get("wf") or "",
        "score": entry.get("score"),
        "conf": entry.get("confidence") or entry.get("conf"),
        "duration_ms": entry.get("duration_ms"),
        "skipped_llm": entry.get("skipped_llm"),
    }
    # 从 message 兜底提取 wf/score（部分日志只在 message 里）
    msg = entry.get("message", "")
    if not out["wf"]:
        mw = re.search(r"wf=([\w\-]+)", msg)
        if mw:
            out["wf"] = mw.group(1)
    if out["score"] is None:
        ms = re.search(r"score=([\d.]+)", msg)
        if ms:
            out["score"] = float(ms.group(1))
    if out["conf"] is None:
        mc = re.search(r"conf=([\d.]+)", msg)
        if mc:
            out["conf"] = float(mc.group(1))
    if out["duration_ms"] is None:
        md = re.search(r"([\d.]+)ms", msg)
        if md:
            out["duration_ms"] = float(md.group(1))
    return out


def _bucket_score(s):
    if s is None:
        return "n/a"
    if s < 0.25:
        return "<0.25"
    if s < 0.4:
        return "0.25-0.4"
    if s < 0.6:
        return "0.4-0.6"
    if s < 0.8:
        return "0.6-0.8"
    return ">=0.8"


def _bucket_conf(c):
    if c is None:
        return "n/a"
    if c < 0.4:
        return "<0.4"
    if c < 0.7:
        return "0.4-0.7"
    if c < 0.9:
        return "0.7-0.9"
    return ">=0.9"


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p))
    return sorted_vals[idx]


def build_report(entries: list[dict]) -> str:
    """生成 Markdown 报表"""
    hits = [e for e in entries if e["action"] == "hit"]
    misses = [e for e in entries if e["action"] == "miss"]
    failed = [e for e in entries if e["action"] == "exec_failed"]
    errors = [e for e in entries if e["action"] == "error"]
    total = len(entries)
    hit_rate = len(hits) / total if total else 0.0

    lines = [
        "# 工作流拦截层日志报表",
        "",
        f"- 拦截总数: **{total}**",
        f"- 命中: {len(hits)} / 未命中: {len(misses)} / 执行失败: {len(failed)} / 异常: {len(errors)}",
        f"- 命中率: **{hit_rate:.1%}**",
        "",
    ]

    # 按工作流 Top-N
    by_wf = defaultdict(list)
    for e in hits:
        by_wf[e["wf"] or "(unknown)"].append(e)
    lines.append("## 按工作流命中 Top")
    lines.append("")
    lines.append("| 工作流 | 命中次数 | 平均 score | 平均 conf |")
    lines.append("| --- | ---: | ---: | ---: |")
    for wf, lst in sorted(by_wf.items(), key=lambda x: -len(x[1])):
        scores = [e["score"] for e in lst if e["score"] is not None]
        confs = [e["conf"] for e in lst if e["conf"] is not None]
        lines.append(
            "| {} | {} | {} | {} |".format(
                wf, len(lst),
                round(statistics.mean(scores), 4) if scores else "-",
                round(statistics.mean(confs), 4) if confs else "-",
            )
        )
    lines.append("")

    # score / conf 分桶
    lines.append("## score 分桶（命中样本）")
    lines.append("")
    score_buckets = defaultdict(int)
    for e in hits:
        score_buckets[_bucket_score(e["score"])] += 1
    for b in ("<0.25", "0.25-0.4", "0.4-0.6", "0.6-0.8", ">=0.8", "n/a"):
        if score_buckets[b]:
            lines.append(f"- `{b}`: {score_buckets[b]}")
    lines.append("")

    lines.append("## confidence 分桶（命中样本）")
    lines.append("")
    conf_buckets = defaultdict(int)
    for e in hits:
        conf_buckets[_bucket_conf(e["conf"])] += 1
    for b in ("<0.4", "0.4-0.7", "0.7-0.9", ">=0.9", "n/a"):
        if conf_buckets[b]:
            lines.append(f"- `{b}`: {conf_buckets[b]}")
    lines.append("")

    # 命中延迟
    durs = sorted(e["duration_ms"] for e in hits if e["duration_ms"] is not None)
    lines.append("## 命中延迟（ms）")
    lines.append("")
    if durs:
        lines.append(
            f"- 均值: {statistics.mean(durs):.2f} / P50: {_pct(durs, 0.5):.2f} / "
            f"P95: {_pct(durs, 0.95):.2f} / P99: {_pct(durs, 0.99):.2f} / 最大: {max(durs):.2f}"
        )
    else:
        lines.append("- 无命中样本")
    lines.append("")
    return "\n".join(lines)


def _demo_entries() -> list[dict]:
    return [
        {"action": "orchestrator.wfl.hit", "workflow_id": "news-en-1", "score": 0.41, "confidence": 0.45, "duration_ms": 8.0, "skipped_llm": True},
        {"action": "orchestrator.wfl.hit", "workflow_id": "news-en-1", "score": 0.50, "confidence": 0.48, "duration_ms": 7.2, "skipped_llm": True},
        {"action": "orchestrator.wfl.hit", "workflow_id": "news-en-1", "score": 0.72, "confidence": 0.55, "duration_ms": 9.1, "skipped_llm": True},
        {"action": "orchestrator.wfl.miss", "duration_ms": 1.0},
        {"action": "orchestrator.wfl.exec_failed", "workflow_id": "wf-x", "error": "工具超时"},
        {"action": "orchestrator.wfl.error", "duration_ms": 0.5},
        {"action": "orchestrator.wfl.learned", "workflow_id": "wf-y"},
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="工作流拦截层日志解析报表")
    parser.add_argument("log_files", nargs="*", help="日志文件路径（支持通配符）")
    parser.add_argument("--top-n", type=int, default=10, help="按工作流 Top-N（默认 10）")
    parser.add_argument("--json", metavar="OUT", help="同时输出 JSON 报表到文件")
    parser.add_argument("--demo", action="store_true", help="用内置样例生成报表验证")
    args = parser.parse_args(argv)

    entries: list[dict] = []
    if args.demo:
        for e in _demo_entries():
            parsed = _extract(e)
            if parsed:
                entries.append(parsed)
    else:
        if not args.log_files:
            parser.error("至少提供一个日志文件，或使用 --demo")
        for pattern in args.log_files:
            for path in Path().glob(pattern) if any(c in pattern for c in "*?[") else [Path(pattern)]:
                if not path.exists():
                    print(f"跳过不存在: {path}", file=sys.stderr)
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        entry = _parse_line(line)
                        if entry:
                            extracted = _extract(entry)
                            if extracted:
                                entries.append(extracted)

    report = build_report(entries)
    print(report)

    if args.json:
        out = {
            "total": len(entries),
            "entries": entries,
            "report_markdown": report,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n[JSON] 报表已写入 {args.json}")

    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
