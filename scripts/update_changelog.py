#!/usr/bin/env python3
"""自动生成 Release 发布备注并更新 CHANGELOG.md

用法:
    python scripts/update_changelog.py --version v1.1.0                  # 预览发布备注（stdout）
    python scripts/update_changelog.py --version v1.1.0 --prev-tag v1.0.0  # 指定上一 tag
    python scripts/update_changelog.py --version v1.1.0 --write           # 写入 CHANGELOG.md 顶部
    python scripts/update_changelog.py --version v1.1.0 --write --out notes.md  # 同时导出发布备注

从 git log 提取 <prev-tag>..HEAD 的 conventional commits，按类型分组生成 Markdown 章节，
输出即 Release 正文来源。不依赖外网，纯 git 本地数据。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

# 类型 → 中文标题（显示顺序）
TYPE_LABELS = [
    ("feat", "新功能"),
    ("fix", "Bug 修复"),
    ("perf", "性能优化"),
    ("refactor", "重构"),
    ("docs", "文档"),
    ("test", "测试"),
    ("ci", "CI/CD"),
    ("chore", "其他"),
]
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{7,40}\s+)?(feat|fix|perf|refactor|docs|test|ci|chore)(?:\(([^)]+)\))?:?\s*(.+)$")


def git(args: list[str]) -> str:
    r = subprocess.run(["git"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout


def prev_tag() -> str | None:
    """取最近第 2 个 tag（最新 tag 视为当前版本）"""
    tags = [t for t in git(["tag", "--sort=-creatordate", "--list"]).strip().splitlines() if t]
    return tags[1] if len(tags) >= 2 else None


def build_notes(version: str, prev: str) -> str:
    if prev:
        log = git(["log", "--oneline", f"{prev}..HEAD"]).strip()
    else:
        log = git(["log", "--oneline"]).strip()
    grouped: dict[str, list[str]] = {}
    uncategorized: list[str] = []
    for line in log.splitlines():
        if not line:
            continue
        m = COMMIT_RE.match(line)
        if m:
            typ, scope, subject = m.group(1), m.group(2) or "", m.group(3).strip()
            suffix = f"（{scope}）" if scope else ""
            grouped.setdefault(typ, []).append(f"- {subject}{suffix}")
        else:
            uncategorized.append(f"- {line}")

    parts = [f"## {version}（{date.today().isoformat()}）\n"]
    total = 0
    for label, items in [(lab, grouped.get(typ)) for typ, lab in TYPE_LABELS] + [("其他", uncategorized)]:
        if items:
            parts.append(f"\n### {label}\n")
            parts.extend(items)
            total += len(items)
    parts.append(f"\n> 共 {total} 条变更。")
    return "\n".join(parts) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="生成发布备注并更新 CHANGELOG.md")
    ap.add_argument("--version", required=True, help="版本号，如 v1.1.0")
    ap.add_argument("--prev-tag", help="上一版本 tag（默认取最近第 2 个 tag）")
    ap.add_argument("--write", action="store_true", help="写入 CHANGELOG.md 顶部")
    ap.add_argument("--out", help="同时导出发布备注到文件")
    args = ap.parse_args()

    prev = args.prev_tag or prev_tag()
    if not prev:
        sys.exit("无法确定上一版本 tag，请用 --prev-tag 显式指定")
    notes = build_notes(args.version, prev)

    print("=== 发布备注预览 ===")
    print(notes)

    if args.out:
        Path(args.out).write_text(notes, encoding="utf-8")
        print(f"发布备注已导出: {args.out}")

    if args.write:
        if not CHANGELOG.exists():
            sys.exit(f"CHANGELOG.md 不存在: {CHANGELOG}")
        content = CHANGELOG.read_text(encoding="utf-8")
        marker = "# CHANGELOG"
        idx = content.find(marker)
        if idx < 0:
            sys.exit("CHANGELOG.md 缺少 '# CHANGELOG' 标题")
        insert_at = idx + len(marker) + 1
        CHANGELOG.write_text(content[:insert_at] + "\n" + notes + content[insert_at:], encoding="utf-8")
        print(f"CHANGELOG.md 已更新: {CHANGELOG}")


if __name__ == "__main__":
    main()
