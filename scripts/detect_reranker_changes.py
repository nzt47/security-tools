"""检测当前分支是否包含 reranker 超时保护相关改动，并生成回滚命令建议

依据: docs/observability/pr_template_reranker_timeout.md 回滚方案
相关文件与 CI paths 守卫一致(4 个)：
    agent/tool_router_reranker.py
    agent/skills_mgmt/reranker_utils.py
    tests/unit/test_reranker_utils.py
    scripts/verify_reranker_timeout_health.py

只读检测(git status/diff/log)，不执行任何修改；回滚命令仅打印建议。

用法:
    python scripts/detect_reranker_changes.py
    python scripts/detect_reranker_changes.py --base origin/main
    python scripts/detect_reranker_changes.py --include-env --json
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 与 .github/workflows/reranker-timeout-guard.yml 的 paths 保持一致
_RELATED_FILES = (
    "agent/tool_router_reranker.py",
    "agent/skills_mgmt/reranker_utils.py",
    "tests/unit/test_reranker_utils.py",
    "scripts/verify_reranker_timeout_health.py",
)

_DEFAULT_BASES = ("origin/main", "main", "develop", "origin/develop")


def _git(args: list[str]) -> subprocess.CompletedProcess:
    """只读 git 命令"""
    return subprocess.run(
        ["git", *args], cwd=_PROJECT_ROOT,
        capture_output=True, text=True, encoding="utf-8",
    )


def _resolve_base(explicit: str | None) -> str | None:
    """解析比较基准: 显式参数优先, 否则按候选顺序探测存在分支"""
    candidates = ([explicit] if explicit else []) + list(_DEFAULT_BASES)
    for base in candidates:
        r = _git(["rev-parse", "--verify", "--quiet", base])
        if r.returncode == 0:
            return base
    return None


def detect_changes(base: str | None = None) -> dict:
    """核心检测逻辑(可导入复用)，返回结构化结果

    Returns:
        dict: branch/base/dirty_related/committed/commits/
              rollback_advice/has_changes
    """
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()

    # 1. 未提交改动(工作区 + 暂存区)
    status = _git(["status", "--porcelain"]).stdout.splitlines()
    dirty_related = []
    for line in status:
        # porcelain 格式: "XY path"（X/Y 为状态标记, 第 4 列起为路径）
        path = line[3:].strip()
        if path in _RELATED_FILES:
            dirty_related.append(path)

    # 2. 已提交改动(base..HEAD)
    base = _resolve_base(base)
    committed = []
    if base:
        diff = _git(["diff", "--name-only", f"{base}...HEAD"])
        committed = [f for f in diff.stdout.splitlines() if f in _RELATED_FILES]

    # 3. 相关 commit 列表(时间从新到旧)
    commits: list[str] = []
    if base and committed:
        log = _git(["log", "--oneline", "--no-merges", f"{base}..HEAD", "--",
                    *sorted(set(_RELATED_FILES))])
        commits = [ln for ln in log.stdout.splitlines() if ln.strip()]

    has_changes = bool(dirty_related or commits)
    return {
        "branch": branch,
        "base": base,
        "dirty_related": dirty_related,
        "committed": committed,
        "commits": commits,
        "has_changes": has_changes,
        "related_files": list(_RELATED_FILES),
        "rollback_advice": _build_advice(dirty_related, commits),
    }


def _build_advice(dirty_related: list[str], commits: list[str]) -> list[str]:
    """生成回滚命令建议列表(文本行)"""
    advice: list[str] = []
    if dirty_related:
        advice.append("[未提交改动] 放弃工作区修改:")
        advice.append(f"    git checkout -- {' '.join(dirty_related)}")
        advice.append("  或暂存后恢复:")
        advice.append(f"    git stash push -- {' '.join(dirty_related)}")
    if commits:
        advice.append(f"[已提交改动] 共 {len(commits)} 个相关 commit:")
        for c in commits:
            advice.append(f"    {c}")
        first_sha = commits[-1].split()[0]
        last_sha = commits[0].split()[0]
        if len(commits) == 1:
            advice.append("回滚命令(撤销最近一个 commit):")
            advice.append(f"    git revert {last_sha}")
        else:
            advice.append("回滚命令(区间):")
            advice.append(f"    git revert --no-commit {first_sha}..{last_sha}")
        advice.append("回滚后验证(复用 CI 守卫):")
        advice.append("    python scripts/verify_reranker_timeout_health.py")
    elif not dirty_related:
        advice.append("无需回滚 —— 当前分支未包含 reranker 相关改动")
    return advice


def main() -> int:
    p = argparse.ArgumentParser(description="检测 reranker 改动并生成回滚建议")
    p.add_argument("--base", help="比较基准分支(默认自动探测 origin/main→main→develop)")
    p.add_argument("--include-env", action="store_true",
                   help="附加 .env 中 AGENT_RERANKER_TIMEOUT 回滚提示")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = p.parse_args()

    result = detect_changes(args.base)
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["tool"] = "detect_reranker_changes"

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print(f"当前分支: {result['branch']} | base: {result['base'] or '未找到基准'}")
        print("相关文件(与 CI 守卫 paths 一致):")
        for f in result["related_files"]:
            print(f"  - {f}")
        print("=" * 64)
        print("[1] 未提交相关改动:", result["dirty_related"] or "无")
        print("[2] 已提交相关改动:", result["committed"] or "无")
        if result["commits"]:
            print("[3] 相关 commit:")
            for c in result["commits"]:
                print(f"    {c}")
        print("\n回滚建议:")
        for line in result["rollback_advice"]:
            print(f"  {line}")
        if args.include_env:
            print("\n  [.env 配置回滚] 删除或调大 AGENT_RERANKER_TIMEOUT(默认 60s 已最宽容)")

    # 有改动 0, 无改动 1（无改动退出码非 0 便于 CI 感知"无事可做"）
    return 0 if result["has_changes"] else 1


if __name__ == "__main__":
    sys.exit(main())
