#!/usr/bin/env python3
"""排查 stash 的创建来源 — 展示每个 stash 的详细信息用于追溯

对每个 stash 显示:
  1. 创建消息和分支 (git stash list 原始行)
  2. 修改文件统计 (git stash show --stat)
  3. 创建时间 (从 commit 元数据提取)
  4. base commit 信息 (stash@{N}^1)
  5. 文件分类摘要 (新增/修改/删除)

用法:
  python scripts/inspect_stash_origins.py              # 排查所有 stash
  python scripts/inspect_stash_origins.py 0 1          # 排查指定序号
  python scripts/inspect_stash_origins.py --json        # JSON 输出
"""
import subprocess
import sys
import json
from datetime import datetime


def run_git(*args):
    """执行 git 命令, 返回 (returncode, stdout, stderr)"""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def get_stash_list():
    """获取 stash 列表 (原始行)"""
    rc, out, _ = run_git("stash", "list")
    if rc != 0 or not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def get_stash_stat(stash_ref):
    """获取 stash 的文件修改统计"""
    rc, out, err = run_git("stash", "show", "--stat", stash_ref)
    if rc != 0:
        return f"[无法获取 stat] {err}"
    return out


def get_stash_commit_info(stash_ref):
    """获取 stash commit 的元数据 (作者、时间、消息)"""
    # stash 是一个 merge commit, 直接查看其元数据
    fmt = "%H%n%an%n%ae%n%ai%n%s"
    rc, out, err = run_git("show", "-s", f"--format={fmt}", stash_ref)
    if rc != 0:
        return None
    lines = out.splitlines()
    if len(lines) < 5:
        return None
    return {
        "hash": lines[0],
        "author_name": lines[1],
        "author_email": lines[2],
        "author_date": lines[3],
        "subject": lines[4],
    }


def get_stash_base(stash_ref):
    """获取 stash 的 base commit (stash@{N}^1 = 创建时的 HEAD)"""
    rc, out, _ = run_git("rev-parse", f"{stash_ref}^1")
    if rc != 0:
        return None
    # 获取 base commit 的简短信息
    rc2, out2, _ = run_git("log", "-1", "--oneline", out)
    if rc2 != 0:
        return out[:12]
    return out2


def classify_files(stash_ref):
    """分类 stash 中的文件: 新增/修改/删除"""
    # 用 git diff --name-status 对比 base 和 stash
    rc, out, _ = run_git("diff", "--name-status", f"{stash_ref}^1", stash_ref)
    if rc != 0:
        return {"added": [], "modified": [], "deleted": [], "renamed": []}
    added, modified, deleted, renamed = [], [], [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("A"):
            added.append(parts[1] if len(parts) > 1 else "?")
        elif status.startswith("M"):
            modified.append(parts[1] if len(parts) > 1 else "?")
        elif status.startswith("D"):
            deleted.append(parts[1] if len(parts) > 1 else "?")
        elif status.startswith("R"):
            renamed.append(" -> ".join(parts[1:]) if len(parts) > 1 else "?")
    return {"added": added, "modified": modified, "deleted": deleted, "renamed": renamed}


def inspect_stash(idx, list_line, as_json=False):
    """排查单个 stash"""
    stash_ref = f"stash@{{{idx}}}"
    info = get_stash_commit_info(stash_ref)
    base = get_stash_base(stash_ref)
    stat = get_stash_stat(stash_ref)
    files = classify_files(stash_ref)

    if as_json:
        return {
            "stash_ref": stash_ref,
            "list_line": list_line,
            "commit_info": info,
            "base_commit": base,
            "file_stats": stat,
            "file_classification": files,
        }

    print(f"\n{'='*70}")
    print(f"  {stash_ref}: {list_line}")
    print(f"{'='*70}")

    if info:
        print(f"\n[创建者信息]")
        print(f"  作者:   {info['author_name']} <{info['author_email']}>")
        print(f"  时间:   {info['author_date']}")
        print(f"  Hash:   {info['hash']}")
        print(f"  主题:   {info['subject']}")

    if base:
        print(f"\n[Base Commit (创建时的 HEAD)]")
        print(f"  {base}")

    print(f"\n[文件修改统计]")
    if stat:
        for line in stat.splitlines():
            print(f"  {line}")
    else:
        print(f"  (无修改或无法获取)")

    print(f"\n[文件分类摘要]")
    print(f"  新增 ({len(files['added'])}):  {files['added'][:5]}{'...' if len(files['added']) > 5 else ''}")
    print(f"  修改 ({len(files['modified'])}):  {files['modified'][:5]}{'...' if len(files['modified']) > 5 else ''}")
    print(f"  删除 ({len(files['deleted'])}):  {files['deleted'][:5]}{'...' if len(files['deleted']) > 5 else ''}")
    if files['renamed']:
        print(f"  重命名 ({len(files['renamed'])}): {files['renamed'][:3]}")

    # 排查线索
    print(f"\n[排查线索]")
    if info:
        dt_str = info['author_date']
        print(f"  创建时间: {dt_str}")
        if "master" in list_line:
            print(f"  创建分支: master (当前分支)")
        elif "no branch" in list_line.lower() or "detached" in list_line.lower():
            print(f"  创建分支: detached HEAD (可能在 rebase/cherry-pick 期间创建)")
        else:
            branch = list_line.split("On ")[1].split(":")[0] if "On " in list_line else "?"
            print(f"  创建分支: {branch}")

    return {"stash_ref": stash_ref, "info": info, "files": files}


def main():
    as_json = "--json" in sys.argv
    target_indices = [int(x) for x in sys.argv[1:] if x.isdigit()]

    stash_lines = get_stash_list()
    if not stash_lines:
        print("[信息] 当前无 stash")
        return

    print(f"[信息] 当前共 {len(stash_lines)} 个 stash")

    if not target_indices:
        target_indices = list(range(len(stash_lines)))

    if as_json:
        results = []
        for idx in target_indices:
            if idx < len(stash_lines):
                results.append(inspect_stash(idx, stash_lines[idx], as_json=True))
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for idx in target_indices:
            if idx < len(stash_lines):
                inspect_stash(idx, stash_lines[idx])

        # 汇总
        print(f"\n{'='*70}")
        print(f"[汇总] 排查了 {len(target_indices)} 个 stash")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
