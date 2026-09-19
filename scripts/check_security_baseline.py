#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安全扫描基线门禁（TASK-03 第 4 步 / E5）

Why 需要这个脚本
----------------
本仓的安全门禁此前全是 `|| true`：

    .github/workflows/ci.yml:255  bandit -r agent/ -f json -o ... || true
    .github/workflows/ci.yml:256  bandit -r agent/ -f screen || true
    .github/workflows/ci.yml:261  safety check --json --output ... || true

`|| true` 的含义是"**永远不阻断**"，于是这两步的实际作用是"在日志里打印一堆字"。
TASK-03 §4 的纪律是"**不是要求立刻全绿，而是要求状态诚实 + 回归可拦**"。
安全类检查做到这两点的手法与 `failures_baseline.txt` 完全一致：

    **历史问题写进基线；只有"新增问题"才阻断。**

Why 用指纹集合而不是数量
------------------------
比数量会被掩盖：修掉 1 个高危、新引入 1 个高危 ⇒ 总数不变 ⇒ 门禁绿灯，
而那是一次**真实的安全回归**。所以按 `test_id:文件:行号` 的集合做差集。

Why 没有把 safety 作为阻断项
----------------------------
`safety` 3.x 起要求登录/API key（`safety check` 在无凭据时不再给出可用结论），
本机与 CI 都没有凭据。把一条拿不到结论的检查挂成"阻断"只会得到一条永远红的
断言，最后必然被改回 `|| true`。故：**阻断项用 `pip-audit`**（无凭据、已声明在
`pyproject.toml` 的 dev 依赖里），`safety` 降级为**归档 + 显式标注未生效**。

退出码
------
0 = 无新增安全问题（或已建立基线）
1 = 出现基线之外的新安全问题
2 = 用法错误
3 = 工具不可用（**不得当作通过**；CI 里应先把它装上）
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "security_baseline.json"

#: 只对这两个严重级做阻断（LOW 噪声大，且不是"安全问题"而多是风格问题）
BLOCKING_SEVERITIES = ("HIGH", "MEDIUM")


def _run(cmd: List[str], timeout: int = 1800) -> tuple[str, str, int]:
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.stdout or "", proc.stderr or "", proc.returncode


def run_bandit(out_path: Path) -> Dict[str, Any]:
    """跑 bandit 并抽取指纹集合。

    bandit 有"发现问题即非零退出"的语义，故 stdout 为空、结果写在 `-o` 文件里。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _out, err, rc = _run([
        sys.executable, "-m", "bandit", "-r", "agent/",
        "-f", "json", "-o", str(out_path), "-q",
    ])
    if not out_path.exists():
        raise FileNotFoundError(f"bandit 未产出报告（rc={rc}）：{err.strip()[:200]}")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    by_sev: Dict[str, List[str]] = {s: [] for s in BLOCKING_SEVERITIES}
    by_sev["LOW"] = []
    for item in data.get("results", []):
        sev = str(item.get("issue_severity", "")).upper()
        fp = "{test}:{file}:{line}".format(
            test=item.get("test_id", "?"),
            file=str(item.get("filename", "?")).replace("\\", "/"),
            line=item.get("line_number", 0),
        )
        by_sev.setdefault(sev, []).append(fp)
    return {
        "total": sum(len(v) for v in by_sev.values()),
        "by_severity": {k: sorted(set(v)) for k, v in by_sev.items()},
        "metrics": data.get("metrics", {}).get("_totals", {}),
    }


def run_pip_audit(out_path: Path) -> Dict[str, Any]:
    """跑 pip-audit（无凭据依赖）并抽取 `包==版本:漏洞ID` 指纹集合。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out, err, rc = _run([sys.executable, "-m", "pip_audit", "-f", "json", "--progress-spinner", "off"])
    text = out.strip() or err.strip()
    if not text:
        raise FileNotFoundError(f"pip-audit 无输出（rc={rc}）")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # pip-audit 把 JSON 打在 stdout；若被日志前缀污染则退化为抓 JSON 片段
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    vulns: List[str] = []
    deps_entries = data.get("dependencies", data) if isinstance(data, dict) else data
    for dep in deps_entries or []:
        name = dep.get("name", "?")
        ver = dep.get("version", "?")
        for v in dep.get("vulns", []) or []:
            vulns.append(f"{name}=={ver}:{v.get('id', '?')}")
    return {"total": len(vulns), "fingerprints": sorted(set(vulns))}


def load_baseline(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"bootstrapped": False}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"bootstrapped": False}


def save_baseline(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="安全扫描基线门禁（新问题阻断、历史问题写基线）")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--report-dir", type=Path, default=REPO_ROOT / "_ci_logs" / "security")
    ap.add_argument("--only", choices=("bandit", "pip-audit"), default=None)
    ap.add_argument("--update", action="store_true", help="用本次结果重建基线（人工评审后使用）")
    args = ap.parse_args(argv)

    baseline = load_baseline(args.baseline)
    bootstrapped = bool(baseline.get("bootstrapped"))
    args.report_dir.mkdir(parents=True, exist_ok=True)

    current: Dict[str, Any] = {}
    missing: List[str] = []

    if args.only in (None, "bandit"):
        try:
            current["bandit"] = run_bandit(args.report_dir / "bandit_report.json")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"bandit（{exc}）")
    if args.only in (None, "pip-audit"):
        try:
            current["pip_audit"] = run_pip_audit(args.report_dir / "pip_audit_report.json")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"pip-audit（{exc}）")

    print("=" * 78)
    print("安全扫描基线门禁")
    print("=" * 78)
    for tool, res in current.items():
        print(f"  {tool}: 共 {res['total']} 项")
    if missing:
        print("  ⚠️ 工具不可用（**不得视为通过**）：")
        for m in missing:
            print(f"     - {m}")

    if not bootstrapped or args.update:
        payload = {
            "bootstrapped": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "why": (
                "历史问题基线：只在'新增'安全问题出现时阻断（与 failures_baseline.txt 同一手法）。"
                "重建命令：python scripts/check_security_baseline.py --update。"
                "重建必须人工评审——把新问题写进基线等于把安全回归洗掉。"
            ),
            **current,
        }
        if missing:
            print("\n[security_baseline] 有工具不可用，**不建立基线**（否则会把'没扫到'当成'没有'）")
            return 3
        save_baseline(args.baseline, payload)
        print(f"\n[security_baseline] 已建立基线 → {args.baseline}")
        print("  ⚠️ 首次建立只记录现状、不做判定；请人工评审 HIGH 项后提交。")
        return 0

    new_findings: List[str] = []
    for sev in BLOCKING_SEVERITIES:
        base_set = set((baseline.get("bandit", {}).get("by_severity", {}) or {}).get(sev, []))
        cur_set = set((current.get("bandit", {}).get("by_severity", {}) or {}).get(sev, []))
        for fp in sorted(cur_set - base_set):
            new_findings.append(f"bandit/{sev}: {fp}")

    base_vulns = set((baseline.get("pip_audit", {}) or {}).get("fingerprints", []))
    cur_vulns = set((current.get("pip_audit", {}) or {}).get("fingerprints", []))
    for fp in sorted(cur_vulns - base_vulns):
        new_findings.append(f"pip-audit: {fp}")

    print(f"\n新增问题: {len(new_findings)}")
    for item in new_findings:
        print(f"  ✗ {item}")

    if missing:
        return 3
    if new_findings:
        print(
            "\n[security_baseline] ✗ 出现基线之外的新安全问题。"
            "请修复；确属误报请把对应指纹加进基线**并在提交说明里交代理由**。",
            file=sys.stderr,
        )
        return 1
    print("\n[security_baseline] ✔ 无新增安全问题（历史问题已固化在基线中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
