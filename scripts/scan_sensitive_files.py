#!/usr/bin/env python3
"""项目敏感信息扫描工具：找出包含密码/Token/密钥模式的文件，并区分是否被 .gitignore 覆盖。

【目的】
  预防"真实凭证被 git add 进版本库"（此前 alertmanager.yml 的同类风险）。
  扫描对象：文本文件中的高价值凭证模式 + 私钥块 + 云平台 Token 前缀。

【用法】
  python scripts/scan_sensitive_files.py                # 扫描全项目，打印结果
  python scripts/scan_sensitive_files.py --json out.json  # 输出 JSON 结果
  python scripts/scan_sensitive_files.py --path <子目录>  # 限定扫描目录

【判定】
  - IGNORED    : 命中但已被 .gitignore 覆盖 → 低风险（git add 不会带上）
  - NOT_IGNORED: 命中且未被忽略            → 高风险（可能被误提交），需处理
  - 报告按 NOT_IGNORED 优先排序，并给出清理建议。

【不易】本脚本只读扫描，不修改/删除任何文件；清理动作由人工执行。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 扫描忽略的目录（运行时/第三方/构建产物，天然不应入库）
SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", ".claude", ".trae", ".cursor",
    "backups", "workspace", "logs", "cache", ".pytest_cache",
    "coverage", ".coverage", "htmlcov", "site-packages", "test_reports",
}
# 扫描忽略的扩展名（二进制/非文本）
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".svg",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".dylib",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".whl",
    ".db", ".sqlite", ".sqlite3", ".pdf", ".lock",
    ".ttf", ".woff", ".woff2", ".eot",
}

# 高价值凭证模式（尽量降低误报）
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
CLOUD_TOKEN_RE = re.compile(
    r"\b(AKIA[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{36,}|"
    r"sk-[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[0-9A-Za-z_-]{35})\b"
)
# 键名 → 引号值。键名清单覆盖中英文常见写法。
SECRET_KEY_RE = re.compile(
    r"\b(?:"
    r"password|passwd|pwd|secret|token|api[_-]?key|apikey|"
    r"access[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key|"
    r"client[_-]?secret|consumer[_-]?secret|"
    r"密码|口令|密钥|授权码"
    r")\b\s*[:=]\s*['\"]([^'\"]{6,})['\"]",
    re.IGNORECASE,
)
# 明显非真实的占位/示例值 → 过滤（避免海量误报）
PLACEHOLDER_RE = re.compile(
    r"REPLACE_WITH|PLACEHOLDER|your[_ -]|example|sample|demo|mock|test|"
    r"xxxx+|yyyy+|zzzz+|change[_ -]me|changeme|dummy|todo|redacted|"
    r"^\**$|123456|abcdef|000000|<[^>]+>|\$\{[^}]+\}",
    re.IGNORECASE,
)


def git_check_ignore(path: Path, cwd: Path) -> bool:
    """判断文件是否被 .gitignore 覆盖。非 git 仓库或 git 不可用时视为未忽略。"""
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=str(cwd), capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def scan_file(path: Path, cwd: Path) -> dict | None:
    """扫描单文件，返回 {path, reasons:[{type,line,snippet}]} 或 None。"""
    try:
        if path.stat().st_size > 4 * 1024 * 1024:  # >4MB 跳过
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

    reasons = []
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if len(line) > 300:  # 过长行（如压缩 JSON）只做前缀匹配
            line = line[:300]
        # 私钥块
        if PRIVATE_KEY_RE.search(line):
            reasons.append({"type": "PRIVATE_KEY", "line": ln, "snippet": line[:120]})
            continue
        # 云 Token 前缀
        m = CLOUD_TOKEN_RE.search(line)
        if m:
            reasons.append({"type": "CLOUD_TOKEN", "line": ln,
                            "snippet": re.sub(CLOUD_TOKEN_RE, "<TOKEN>", line)[:120]})
            continue
        # 键值对凭证（打码显示值，避免泄露）
        m = SECRET_KEY_RE.search(line)
        if m:
            value = m.group(1)
            if PLACEHOLDER_RE.search(value):
                continue
            masked = f"{value[:2]}***{value[-2:]}" if len(value) > 6 else "***"
            reasons.append({"type": "SECRET_KEY", "line": ln,
                            "snippet": line[:120].replace(value, masked)})

    if not reasons:
        return None
    return {"path": str(path), "ignored": git_check_ignore(path, cwd), "reasons": reasons}


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描项目中的敏感信息文件")
    parser.add_argument("--path", default=".", help="扫描根目录（默认项目根）")
    parser.add_argument("--json", default=None, help="结果输出到 JSON 文件")
    parser.add_argument("--max-files", type=int, default=200,
                        help="最多输出的文件数（默认 200，防刷屏）")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    results: list[dict] = []
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in SKIP_EXTS:
                continue
            scanned += 1
            r = scan_file(p, root)
            if r:
                results.append(r)

    # 排序：未忽略优先
    results.sort(key=lambda r: (r["ignored"], r["path"]))

    not_ignored = [r for r in results if not r["ignored"]]
    ignored = [r for r in results if r["ignored"]]

    print("═══ 敏感信息扫描报告 ═══")
    print(f"扫描文件数: {scanned}   命中文件: {len(results)}")
    print(f"  [高风险] 未被 .gitignore 覆盖: {len(not_ignored)}")
    print(f"  [低风险] 已被 .gitignore 覆盖: {len(ignored)}")

    if not_ignored:
        print("\n── [高风险] 未被忽略（可能被 git add 误提交）──")
        for r in not_ignored[: args.max_files]:
            print(f"  ⚠️ {r['path']}")
            for reason in r["reasons"][:5]:
                print(f"      L{reason['line']:<6} {reason['type']:<12} {reason['snippet']}")
            if len(r["reasons"]) > 5:
                print(f"      ... 共 {len(r['reasons'])} 处")

    if ignored:
        print("\n── [低风险] 已被忽略（git add 不会带上）──")
        for r in ignored[:50]:
            print(f"  · {r['path']} ({len(r['reasons'])} 处)")

    print("\n── 清理建议 ──")
    if not not_ignored:
        print("  未发现未被忽略的高风险文件，现状良好。")
    else:
        print(f"  1. 对 {len(not_ignored)} 个高风险文件逐个人工核查；确认含真实凭证的：")
        print("      · 若为运行时产物/本地配置 → 追加到 .gitignore")
        print("      · 若已误提交过 → 立即轮换凭证 + 用 git filter-repo/BFG 重写历史")
        print("      · 若为文档示例 → 改为占位符 <YOUR_TOKEN> 并注明")
        print("  2. 用 git status --short 复核：git add 前检查暂存区不含敏感文件")

    if args.json:
        out = {"scanned": scanned, "high": not_ignored, "low": ignored}
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 结果已写入: {args.json}")

    return 1 if not_ignored else 0


if __name__ == "__main__":
    sys.exit(main())
