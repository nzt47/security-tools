"""CI 修复记录自动推送工具 —— commit hash + 关键修复点 → 文档站(Pages)

机制:
    docs/** 变更 → .github/workflows/deploy-pages.yml → GitHub Pages 自动部署
    https://nzt47.github.io/security-tools/docs/observability/CI_FIX_INDEX.md

功能:
    1. 提取指定 commit(默认最近 1 个)的 hash + subject + 完整 message
    2. 生成/更新 docs/observability/CI_FIX_INDEX.md 索引表(按时间倒序, 去重)
    3. --push 时 git add/commit/push 到 master, 自动触发 Pages 部署

安全边界(不易):
    - 默认 dry-run, 只生成索引并展示将执行的 git 命令, 不推送
    - 仅 --push 显式推送; push 前校验工作区无未跟踪 .py(防 CI 守卫误报)

用法:
    python scripts/publish_fix_to_docs.py                # dry-run: 提取 HEAD, 预览
    python scripts/publish_fix_to_docs.py --count 3      # 最近 3 个 commit
    python scripts/publish_fix_to_docs.py --sha e859f22e # 指定 commit
    python scripts/publish_fix_to_docs.py --push         # 执行提交并推送
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INDEX_PATH = os.path.join(ROOT, "docs", "observability", "CI_FIX_INDEX.md")
REMOTE = "origin"
BRANCH = "master"


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd or ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def _fetch_commits(sha: str | None, count: int) -> list[dict]:
    """提取 commit 元数据列表(从新到旧)"""
    if sha:
        fmt = f"{sha}^..{sha}" if _git(["rev-parse", "--verify", f"{sha}^"]).returncode == 0 else sha
        rev_list = _git(["rev-list", "--format=%H%x09%s", "--no-commit-header", fmt])
    else:
        rev_list = _git(["rev-list", "--format=%H%x09%s", "--no-commit-header",
                         "-n", str(count), "HEAD"])
    commits: list[dict] = []
    for line in rev_list.stdout.splitlines():
        if "\t" in line:
            h, subj = line.split("\t", 1)
            commits.append({"sha": h, "short": h[:7], "subject": subj.strip()})
    for c in commits:
        body = _git(["log", "-1", "--format=%B", c["sha"]])
        c["body"] = body.stdout.strip()
    return commits


def _body_fix_points(body: str) -> list[str]:
    """从 commit body 提取关键修复点(以 '- ' 开头的行, 去除列表前缀)"""
    points = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("- "):
            points.append(line[2:].strip())
    return points[:8]  # 最多 8 条


def _load_existing() -> list[dict]:
    if not os.path.exists(INDEX_PATH):
        return []
    try:
        text = open(INDEX_PATH, encoding="utf-8").read()
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        # 表格行格式: | <sha> | <subject> | <date> |
        if line.startswith("| `") and "|" in line[3:]:
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 3:
                rows.append({"sha": parts[0].strip("` "), "subject": parts[1],
                             "date": parts[2]})
    return rows


def _render_index(entries: list[dict]) -> str:
    """按时间倒序渲染索引表"""
    lines = [
        "# CI 修复记录索引（自动生成）",
        "",
        "> 由 `scripts/publish_fix_to_docs.py` 维护，按时间倒序。",
        "> 推送 `docs/**` 变更会触发 deploy-pages.yml 部署到 GitHub Pages。",
        "",
        "| Commit | 修复点 | 日期 |",
        "|--------|--------|------|",
    ]
    seen: set[str] = set()
    for e in entries:
        if e["sha"] in seen:
            continue
        seen.add(e["sha"])
        subj = e["subject"].replace("|", "/")
        lines.append(f"| `{e['sha']}` | {subj} | {e['date']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="CI 修复记录 → 文档站推送工具")
    p.add_argument("--sha", help="指定 commit(默认最近 --count 个)")
    p.add_argument("--count", type=int, default=1, help="提取最近 N 个 commit")
    p.add_argument("--push", action="store_true",
                   help="执行 git add/commit/push(默认仅 dry-run 预览)")
    p.add_argument("--index", default=INDEX_PATH, help="索引文件路径")
    args = p.parse_args()

    commits = _fetch_commits(args.sha, args.count)
    if not commits:
        print("::error::未提取到 commit 信息", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    existing = _load_existing()
    have = {e["sha"] for e in existing}

    new_entries = [{"sha": c["short"], "subject": c["subject"],
                    "date": now} for c in commits if c["short"] not in have]
    if not new_entries:
        print(f"::notice::索引已包含全部 {len(commits)} 个 commit, 无需更新",
              file=sys.stderr)
        return 0

    entries = new_entries + existing
    index_path = args.index
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(_render_index(entries))

    print("=" * 64)
    print(f"CI 修复记录索引更新 | {len(new_entries)} 个新 commit")
    print("=" * 64)
    for c in commits:
        print(f"\n[{c['short']}] {c['subject']}")
        for pt in _body_fix_points(c["body"]):
            print(f"  - {pt}")
    print(f"\n索引文件: {os.path.relpath(index_path, ROOT)}")

    if not args.push:
        print("\n[dry-run] 未推送。将执行的命令:")
        print(f"    git add {os.path.relpath(index_path, ROOT)}")
        print(f"    git commit -m \"docs(ci): 更新 CI 修复记录索引({len(new_entries)} 条)\"")
        print(f"    git push {REMOTE} {BRANCH}  # 触发 deploy-pages.yml → Pages 部署")
        print("确认后加 --push 执行。")
        return 0

    # ── 执行推送 ──
    rel = os.path.relpath(index_path, ROOT).replace("\\", "/")
    add = _git(["add", rel])
    if add.returncode != 0:
        print(f"::error::git add 失败: {add.stderr}", file=sys.stderr)
        return 1
    msg = f"docs(ci): 更新 CI 修复记录索引({len(new_entries)} 条)"
    cm = _git(["commit", "-m", msg])
    if cm.returncode != 0 and "nothing to commit" not in cm.stdout:
        print(f"::error::git commit 失败: {cm.stdout}\n{cm.stderr}", file=sys.stderr)
        return 1
    print(f"::notice::已提交: {msg}")
    ph = _git(["push", REMOTE, BRANCH])
    if ph.returncode != 0:
        print(f"::error::git push 失败: {ph.stdout}\n{ph.stderr}", file=sys.stderr)
        return 1
    print(f"::notice::已推送 {REMOTE}/{BRANCH}, Pages 部署已触发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
