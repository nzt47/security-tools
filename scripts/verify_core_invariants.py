#!/usr/bin/env python3
"""核心不变量监控: 校验关键文件不变量未被破坏(防回滚回归)

背景(2026-08-04 复盘): tool_trace.py 的 flush 异步竞态计数机制 与
hook_fail_safe.psm1 的三段式模板(编码检查 + CI 守卫)曾被回滚到旧状态,
git 历史无对应提交, 导致压测工具 AttributeError、hook 只剩预检段。
本脚本静态校验这些不变量, 集成进 pre-commit hook 在每次提交前拦截。

校验范围(静态度, 不执行业务逻辑):
  T1-T5  agent/observability/tool_trace.py   计数机制(_enqueue/_commit/_count_lock/
                                               _mark_committed/flush 快照等待)
  H1-H4  scripts/dev/hook_fail_safe.psm1     三段式模板 + UTF-8 BOM 契约
  D1-D2  .git/hooks/pre-commit(部署态, 存在才查)  含编码检查/CI 守卫段

分级: 任一 [BLOCK] → exit 1(阻止提交); 全通过 → exit 0。
跳过: SKIP_INVARIANT=1 环境变量(hook 段已内置); 脚本缺失静默跳过(跨仓库安全)。

用法:
    python scripts/verify_core_invariants.py                       # 校验当前仓库
    python scripts/verify_core_invariants.py --repo-root <路径>    # 指定仓库根
    python scripts/verify_core_invariants.py --quiet               # 仅输出 BLOCK
    python scripts/verify_core_invariants.py --json                # 机器可读(stdout 仅 JSON)
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 通用报告生成器(同目录): JSON/文本/HTML 三格式统一输出
sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_generator as rg

_BOM = b"\xef\xbb\xbf"


@dataclass
class Invariant:
    id: str
    rel_path: str          # 相对 repo-root 的文件路径
    desc: str
    patterns: list[str] = field(default_factory=list)  # 全部须存在(正则)
    need_bom: bool = False                              # 文件须带 UTF-8 BOM
    required: bool = True                               # False=文件缺失时跳过

    def label(self) -> str:
        return f"[{self.id}] {self.rel_path}: {self.desc}"


# ═══ tool_trace.py 计数机制(flush 异步竞态修复) ═══
_TOOL_TRACE_INVARIANTS = [
    Invariant(
        id="T1", rel_path="agent/observability/tool_trace.py",
        desc="__init__ 声明计数三件套(_enqueue_count/_commit_count/_count_lock)",
        patterns=[
            r"self\._enqueue_count\s*=\s*0",
            r"self\._commit_count\s*=\s*0",
            r"self\._count_lock\s*=\s*threading\.Lock\(\)",
        ],
    ),
    Invariant(
        id="T2", rel_path="agent/observability/tool_trace.py",
        desc="record() 入队成功后通过锁递增 _enqueue_count",
        patterns=[r"with self\._count_lock:", r"_enqueue_count\s*\+=\s*1"],
    ),
    Invariant(
        id="T3", rel_path="agent/observability/tool_trace.py",
        desc="_write_to_db 两条路径结尾调 _mark_committed(落盘/降级均计数)",
        patterns=[r"_mark_committed\s*\(\s*len\(records\)\s*\)"],
    ),
    Invariant(
        id="T4", rel_path="agent/observability/tool_trace.py",
        desc="_mark_committed 定义: _commit_count 递增",
        patterns=[r"def _mark_committed", r"_commit_count\s*\+=\s*n"],
    ),
    Invariant(
        id="T5", rel_path="agent/observability/tool_trace.py",
        desc="flush() 快照 enqueue 后等待 commit 追平(committed >= target)",
        patterns=[r"target\s*=\s*self\._enqueue_count",
                  r"committed\s*>=\s*target"],
    ),
]

# ═══ hook_fail_safe.psm1 三段式模板(预检 → 编码检查 → CI 守卫) ═══
_HOOK_TEMPLATE_INVARIANTS = [
    Invariant(
        id="H1", rel_path="scripts/dev/hook_fail_safe.psm1",
        desc="Get-HookContent 含编码检查段(ENCODING_CHECK + --quiet --repo-root)",
        patterns=[r"ENCODING_CHECK\s*=", r"--quiet\s+--repo-root"],
    ),
    Invariant(
        id="H2", rel_path="scripts/dev/hook_fail_safe.psm1",
        desc="Get-HookContent 含 CI 守卫段(CI_GUARD + --assert-allowed)",
        patterns=[r"CI_GUARD\s*=", r"--assert-allowed"],
    ),
    Invariant(
        id="H3", rel_path="scripts/dev/hook_fail_safe.psm1",
        desc="含跳过开关 SKIP_ENCODING_CHECK / SKIP_CI_GUARD",
        patterns=[r"SKIP_ENCODING_CHECK", r"SKIP_CI_GUARD"],
    ),
    Invariant(
        id="H4", rel_path="scripts/dev/hook_fail_safe.psm1",
        desc="UTF-8 BOM 契约(PS 5.1 中文系统无 BOM 会乱码解析失败)",
        patterns=[r"# 检测 hook 关键文件"],
        need_bom=True,
    ),
]

# ═══ 部署态 .git/hooks/pre-commit(存在才查) ═══
_DEPLOY_INVARIANTS = [
    Invariant(
        id="D1", rel_path=".git/hooks/pre-commit",
        desc="部署的 hook 含编码检查段",
        patterns=[r"ENCODING_CHECK\s*="],
        required=False,
    ),
    Invariant(
        id="D2", rel_path=".git/hooks/pre-commit",
        desc="部署的 hook 含 CI 守卫段",
        patterns=[r"CI_GUARD\s*="],
        required=False,
    ),
    Invariant(
        id="D3", rel_path=".git/hooks/pre-commit",
        desc="部署的 hook 含核心不变量校验段",
        patterns=[r"INVARIANT\s*=", r"verify_core_invariants\.py"],
        required=False,
    ),
]


def _check_file(path: Path, inv: Invariant) -> tuple[bool, str]:
    """校验单个不变量: 返回 (通过?, 失败原因)"""
    if not path.exists():
        if inv.required:
            return False, f"文件缺失(期望存在)"
        return True, "文件不存在, 跳过(required=False)"
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    missing = [p for p in inv.patterns
               if not re.search(p, text)]
    if inv.need_bom and not raw.startswith(_BOM):
        missing.append("<UTF-8 BOM>")
    if missing:
        return False, f"缺少模式: {' | '.join(missing)}"
    return True, "模式全部存在"


def _run(repo_root: Path, quiet: bool) -> list[dict]:
    results: list[dict] = []
    all_inv = (_TOOL_TRACE_INVARIANTS + _HOOK_TEMPLATE_INVARIANTS
               + _DEPLOY_INVARIANTS)
    for inv in all_inv:
        path = repo_root / inv.rel_path
        ok, detail = _check_file(path, inv)
        results.append({
            "id": inv.id, "path": inv.rel_path, "desc": inv.desc,
            "status": "pass" if ok else "BLOCK",
            "detail": detail,
        })
        if not quiet:
            mark = "PASS" if ok else "BLOCK"
            print(f"  [{mark}] {inv.label()}  {detail}")
        elif not ok:
            print(f"[BLOCK] {inv.label()}  {detail}", file=sys.stderr)
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=str(PROJECT_ROOT),
                   help="仓库根目录(默认脚本所在仓库)")
    p.add_argument("--quiet", action="store_true",
                   help="仅输出 BLOCK 项(供 hook 使用, 保持输出干净)")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告(stdout 仅 JSON)")
    p.add_argument("--html", metavar="PATH", default="",
                   help="导出自包含 HTML 报告到指定路径")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    repo_root = Path(args.repo_root).resolve()
    # --json/--html 模式 stdout 不承载人类可读输出(走 stderr)
    results = _run(repo_root, quiet=args.quiet or args.json or bool(args.html))
    blocked = [r for r in results if r["status"] == "BLOCK"]

    # 通用报告: 统一 JSON/文本/HTML 三格式(report_generator 封装)
    report = rg.build_report(
        tool="verify_core_invariants",
        items=[{k: r[k] for k in ("id", "path", "desc", "status", "detail")}
               for r in results],
        meta={"repo_root": str(repo_root)},
    )

    if args.html:
        path = Path(args.html)
        path.write_text(rg.to_html(report), encoding="utf-8")
        print(f"[INFO] HTML 报告已写入: {path.resolve()}", file=sys.stderr)

    if args.json:
        print(rg.to_json(report))
        if blocked:
            print("::error::verify_core_invariants 检测到不变量被破坏",
                  file=sys.stderr)
        return 0 if not blocked else 1

    if not args.quiet:
        print("")
    print(rg.to_text(report) + (", 阻止提交" if blocked else ""))
    return 0 if not blocked else 1


if __name__ == "__main__":
    # 【修复】Windows CI runner 默认 stdout 编码为 cp1252，输出中文报告时
    # UnicodeEncodeError 崩溃，导致 hook 误判「提交被阻止」
    # （test_precommit_hook_blocking 回归测试失败）。强制 UTF-8 输出。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
