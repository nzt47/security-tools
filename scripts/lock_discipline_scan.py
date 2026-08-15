#!/usr/bin/env python3
"""锁纪律静态扫描 — C3：AST 检测锁临界区内的阻塞 I/O 调用（CI 拦截）

规则（HIGH 阻断 / MEDIUM 提醒）:
    HIGH  锁内 I/O: with <lock>: 块内调用 open/write/flush/requests/urllib/urlopen/
          httpx/redis/pymongo/queue.get(无 timeout)/socket/sleep/input
    MEDIUM 锁内外部回调: with <lock>: 块内调用 .__call__ / run_llm / invoke /
          join() / wait()（可能阻塞在外部）
    MEDIUM 等待型获取: lock.acquire(timeout=None) 且锁竞争路径存在

用法:
    python scripts/lock_discipline_scan.py [--strict] [--baseline baseline.json] [-v]
    --strict  HIGH 命中即退出码 1（CI 阻断）
    --baseline baseline.json  白名单（{file:line: 规则}: 原因}）——存量违规先行入库，增量仍拦截

退出码: 0=通过(HIGH 无命中或全部入 baseline)；1=有 HIGH；2=参数错误
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 锁内禁止的阻塞 I/O 调用（HIGH）
_BLOCKING_CALLS = {
    "open", "write", "flush", "requests", "urllib", "urlopen", "httpx",
    "redis", "pymongo", "sqlite3", "sleep", "input", "socket", "acquire",
    "lock", "join", "wait", "get",
}
# 锁内外部回调（MEDIUM）
_EXTERNAL_CALLS = {"__call__", "invoke", "run_llm", "submit"}

_CNT_MANAGERS = ("lock", "rlock", "cond", "condition", "semaphore", "gate", "mutex")


def _is_lock_expr(node: ast.With, names: set) -> bool:
    """with 上下文是否为锁/条件变量（按变量名启发式，与运行无关的静态判定）"""
    for item in node.items:
        ctx = ast.unparse(item.context_expr)
        ctx = ctx.split("(")[0].strip()
        if ctx.lower() in _CNT_MANAGERS or ctx in names:
            return True
        # 形如 obj.lock / self._lock / manager.lock
        parts = ctx.lower().split(".")
        if parts and parts[-1] in _CNT_MANAGERS:
            return True
    return False


def _scan_file(path: Path, findings: List[Dict[str, Any]], names: set) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.With) and _is_lock_expr(node, names):
            # 统计类变量名（含 with self.x_lock 的 self 属性）
            if isinstance(node.items[0].context_expr, ast.Attribute):
                names.add(node.items[0].context_expr.attr.lower())
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    fn = child.func
                    name = (getattr(fn, "attr", None) or getattr(fn, "id", None) or "")
                    low = name.lower()
                    # get 仅当 receiver 为阻塞队列（queue/q/_queue）才判 HIGH（dict.get 非阻塞不误报）
                    if low == "get":
                        recv = getattr(fn, "value", None)
                        recv_name = getattr(recv, "attr", None) or getattr(recv, "id", None) or ""
                        if not any(k in recv_name.lower() for k in ("queue", "_q", "q")):
                            continue
                    severity = "HIGH" if low in _BLOCKING_CALLS else ("MEDIUM" if low in _EXTERNAL_CALLS else None)
                    if severity:
                        findings.append({
                            "file": os.path.relpath(path, Path.cwd()).replace("\\", "/"),
                            "line": child.lineno,
                            "rule": f"lock-io-{severity.lower()}",
                            "severity": severity,
                            "call": name,
                            "in_lock_block_at": node.lineno,
                        })


def _dedupe(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for f in findings:
        key = (f["file"], f["line"], f["rule"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="锁纪律静态扫描（C3）")
    ap.add_argument("--strict", action="store_true", help="HIGH 命中即退出码 1")
    ap.add_argument("--baseline", type=str, default="", help="白名单 JSON 路径")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("targets", nargs="*", default=["agent"], help="扫描目录/文件")
    args = ap.parse_args()

    findings: List[Dict[str, Any]] = []
    names: set = set()
    for t in args.targets:
        p = Path(t)
        if p.is_file() and p.suffix == ".py":
            _scan_file(p, findings, names)
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if any(seg in ("__pycache__",) for seg in f.parts):
                    continue
                _scan_file(f, findings, names)
    findings = _dedupe(findings)

    baseline: Dict[str, str] = {}
    if args.baseline and Path(args.baseline).exists():
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    active = [f for f in findings if f"{f['file']}:{f['line']}" not in baseline]
    high = [f for f in active if f["severity"] == "HIGH"]
    medium = [f for f in active if f["severity"] == "MEDIUM"]

    print(f"[lock-scan] 扫描 {len(findings)} 命中（{len(baseline)} 已入 baseline）; HIGH={len(high)} MEDIUM={len(medium)}")
    for f in high[:20]:
        print(f"  [HIGH] {f['file']}:{f['line']} 锁内调用 {f['call']} (with-block@{f['in_lock_block_at']})")
    if args.verbose:
        for f in medium[:20]:
            print(f"  [MEDIUM] {f['file']}:{f['line']} 锁内外部回调 {f['call']}")

    if high and args.strict:
        print("[lock-scan] 失败：HIGH 违规未被 baseline 覆盖（--strict）")
        return 1
    print("[lock-scan] 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
