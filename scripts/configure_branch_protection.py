#!/usr/bin/env python3
"""GitHub 分支保护规则配置脚本

使用 GitHub REST API 配置 main 分支保护规则，将 architecture-check 设为必需状态检查。

前置条件：
1. 设置环境变量 GITHUB_TOKEN（需要 repo 权限的 Personal Access Token）
   或在 GitHub Actions 中自动使用 GITHUB_SECRET

2. 远程仓库已存在

使用方式：
    # 本地运行（需设置 GITHUB_TOKEN 环境变量）
    set GITHUB_TOKEN=ghp_xxxxxxxxxxxx
    python scripts/configure_branch_protection.py

    # 指定仓库
    python scripts/configure_branch_protection.py --repo my-org/security-tools

    # 仅查看当前配置（不修改）
    python scripts/configure_branch_protection.py --dry-run

API 文档：
    https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

# 结构化日志
import logging

logger = logging.getLogger(__name__)

# GitHub API 基础 URL
GITHUB_API_BASE = "https://api.github.com"

# 必需的状态检查列表（architecture-check 是核心检查项）
REQUIRED_STATUS_CHECKS = [
    "architecture-check",  # 架构规则校验（阻断合并）
]


def _github_api_request(
    method: str,
    url: str,
    token: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """发送 GitHub API 请求

    Args:
        method: HTTP 方法（GET/PUT/POST/DELETE）
        url: 完整 API URL
        token: GitHub Personal Access Token
        data: 请求体数据

    Returns:
        响应 JSON

    Raises:
        RuntimeError: API 请求失败
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            response_data = json.loads(body.decode("utf-8")) if body else {}
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                '{"trace_id":"branch_protection","module_name":"configure_branch_protection",'
                '"action":"api_request","method":"%s","url":"%s","status":"%d",'
                '"duration_ms":%.2f}',
                method, url, resp.status, elapsed_ms,
            )
            return response_data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error(
            '{"trace_id":"branch_protection","module_name":"configure_branch_protection",'
            '"action":"api_error","method":"%s","url":"%s","status_code":"%d",'
            '"error":"%s","duration_ms":%.2f}',
            method, url, e.code, error_body[:200], elapsed_ms,
        )
        raise RuntimeError(
            f"GitHub API 请求失败: {method} {url} → {e.code}: {error_body[:500]}"
        ) from e


def get_current_protection(
    repo: str, branch: str, token: str
) -> Optional[Dict[str, Any]]:
    """获取当前分支保护配置

    Args:
        repo: 仓库名（owner/repo 格式）
        branch: 分支名
        token: GitHub Token

    Returns:
        当前保护配置，无保护时返回 None
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/branches/{branch}/protection"
    try:
        return _github_api_request("GET", url, token)
    except RuntimeError as e:
        if "404" in str(e):
            logger.info("分支 %s 当前无保护规则", branch)
            return None
        raise


def configure_branch_protection(
    repo: str,
    branch: str,
    token: str,
    required_checks: list[str],
    require_pr_reviews: bool = True,
    required_approving_review_count: int = 1,
    dismiss_stale_reviews: bool = True,
    require_code_owner_reviews: bool = False,
    restrict_pushes: bool = True,
    allow_force_pushes: bool = False,
    allow_deletions: bool = False,
    strict_status_checks: bool = True,
) -> Dict[str, Any]:
    """配置分支保护规则

    Args:
        repo: 仓库名（owner/repo）
        branch: 分支名
        token: GitHub Token
        required_checks: 必需状态检查列表
        require_pr_reviews: 是否要求 PR 审批
        required_approving_review_count: 要求审批人数
        dismiss_stale_reviews: 新提交时驳回旧审批
        require_code_owner_reviews: 是否要求 CODEOWNERS 审批
        restrict_pushes: 限制直推
        allow_force_pushes: 允许 force push
        allow_deletions: 允许删除分支
        strict_status_checks: 要求分支最新后再合并

    Returns:
        API 响应
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/branches/{branch}/protection"

    # 检测仓库类型：个人仓库不支持 restrictions 字段（仅组织仓库可用）
    # 个人仓库通过 enforce_admins + required_pull_request_reviews 已足够限制直推
    repo_info = _github_api_request(
        "GET", f"{GITHUB_API_BASE}/repos/{repo}", token
    )
    is_org_repo = repo_info.get("owner", {}).get("type") == "Organization"

    protection_config: Dict[str, Any] = {
        "required_status_checks": {
            "strict": strict_status_checks,
            "contexts": required_checks,
        },
        "enforce_admins": True,
        "required_pull_request_reviews": (
            {
                "dismiss_stale_reviews": dismiss_stale_reviews,
                "require_code_owner_reviews": require_code_owner_reviews,
                "required_approving_review_count": required_approving_review_count,
            }
            if require_pr_reviews
            else None
        ),
        "allow_force_pushes": allow_force_pushes,
        "allow_deletions": allow_deletions,
        "required_linear_history": True,
    }

    # 仅组织仓库可以设置 restrictions（限制特定用户/团队推送）
    # 个人仓库必须设为 null（不能省略，否则 API 返回 422）
    if restrict_pushes and is_org_repo:
        protection_config["restrictions"] = {"users": [], "teams": []}
    else:
        protection_config["restrictions"] = None

    # 移除 None 值（但 restrictions 必须保留为 null）
    protection_config = {
        k: v for k, v in protection_config.items() if v is not None or k == "restrictions"
    }

    logger.info(
        '{"trace_id":"branch_protection","module_name":"configure_branch_protection",'
        '"action":"configure","repo":"%s","branch":"%s","is_org_repo":%s,'
        '"required_checks":%s}',
        repo, branch, str(is_org_repo).lower(), json.dumps(required_checks),
    )

    return _github_api_request("PUT", url, token, protection_config)


def verify_protection(
    repo: str, branch: str, token: str, expected_checks: list[str]
) -> bool:
    """验证分支保护规则是否生效

    Args:
        repo: 仓库名
        branch: 分支名
        token: GitHub Token
        expected_checks: 期望的必需检查列表

    Returns:
        True=验证通过，False=未通过
    """
    protection = get_current_protection(repo, branch, token)
    if not protection:
        logger.error("分支 %s 无保护规则", branch)
        return False

    actual_checks = protection.get("required_status_checks", {}).get("contexts", [])
    missing = [c for c in expected_checks if c not in actual_checks]

    if missing:
        logger.error(
            '{"trace_id":"branch_protection","module_name":"configure_branch_protection",'
            '"action":"verify_failed","missing_checks":%s,"actual_checks":%s}',
            json.dumps(missing), json.dumps(actual_checks),
        )
        print(f"❌ 验证失败：缺少必需检查: {missing}")
        print(f"   当前检查项: {actual_checks}")
        return False

    print(f"✅ 验证通过：所有必需检查已配置")
    print(f"   必需检查项: {actual_checks}")
    print(f"   强制管理员遵守: {protection.get('enforce_admins', {}).get('enabled', False)}")
    print(f"   要求 PR 审批: {protection.get('required_pull_request_reviews') is not None}")
    print(f"   禁止 force push: {not protection.get('allow_force_pushes', {}).get('enabled', False)}")
    print(f"   禁止删除分支: {not protection.get('allow_deletions', {}).get('enabled', False)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="配置 GitHub 分支保护规则，将 architecture-check 设为必需检查"
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="仓库名（owner/repo），默认从 git remote 自动检测",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="要保护的分支名（默认: main）",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub Token（默认从 GITHUB_TOKEN 环境变量读取）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅查看当前配置，不修改",
    )
    parser.add_argument(
        "--no-pr-reviews",
        action="store_true",
        help="不要求 PR 审批（默认要求 1 人审批）",
    )
    parser.add_argument(
        "--review-count",
        type=int,
        default=1,
        help="要求审批人数（默认: 1）",
    )
    parser.add_argument(
        "--extra-checks",
        nargs="*",
        default=[],
        help="额外的必需状态检查（如 ci/circleci: build）",
    )
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 获取 Token
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token and not args.dry_run:
        print("❌ 错误：未设置 GITHUB_TOKEN 环境变量")
        print("   请设置: set GITHUB_TOKEN=ghp_xxxxxxxxxxxx")
        print("   或使用 --token 参数")
        print("   或使用 --dry-run 查看配置参数预览（无需 Token）")
        return 1

    # 自动检测仓库名
    repo = args.repo
    if not repo:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, cwd=str(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ),
            )
            remote_url = result.stdout.strip()
            # 解析 https://github.com/owner/repo.git 或 git@github.com:owner/repo.git
            if "github.com" in remote_url:
                if remote_url.startswith("https://"):
                    repo = remote_url.replace("https://github.com/", "").replace(".git", "")
                elif remote_url.startswith("git@"):
                    repo = remote_url.split(":")[1].replace(".git", "")
        except Exception:
            pass

    if not repo:
        print("❌ 错误：无法自动检测仓库名，请使用 --repo 参数指定")
        return 1

    all_checks = REQUIRED_STATUS_CHECKS + args.extra_checks

    print(f"📦 仓库: {repo}")
    print(f"🌿 分支: {args.branch}")
    print(f"🔒 必需检查: {all_checks}")
    print()

    # 查看当前配置（需要 Token）
    if token:
        print("─── 当前分支保护配置 ───")
        current = get_current_protection(repo, args.branch, token)
        if current:
            checks = current.get("required_status_checks", {}).get("contexts", [])
            print(f"  当前必需检查: {checks if checks else '（无）'}")
            print(f"  强制管理员: {current.get('enforce_admins', {}).get('enabled', False)}")
        else:
            print("  （无保护规则）")
        print()
    else:
        print("─── 当前分支保护配置 ───")
        print("  （⚠️ 未设置 GITHUB_TOKEN，无法获取当前配置）")
        print()

    if args.dry_run:
        print("🔍 --dry-run 模式，以下是将要配置的参数预览：")
        print()
        print("─── 配置参数预览 ───")
        print(f"  📦 仓库:          {repo}")
        print(f"  🌿 分支:          {args.branch}")
        print(f"  🔒 必需检查:      {all_checks}")
        print(f"  👮 强制管理员:    是")
        print(f"  📝 要求 PR 审批:  {'是' if not args.no_pr_reviews else '否'}")
        if not args.no_pr_reviews:
            print(f"  👥 审批人数:      {args.review_count}")
            print(f"  🔄 驳回旧审批:    是")
        print(f"  🚫 禁止 force push: 是")
        print(f"  🗑️ 禁止删除分支:   是")
        print(f"  📐 线性历史:      是")
        print(f"  🔗 限制直推:      是")
        print()
        print("─── JSON 配置体 ───")
        preview_config = {
            "required_status_checks": {
                "strict": True,
                "contexts": all_checks,
            },
            "enforce_admins": True,
            "required_pull_request_reviews": (
                {
                    "dismiss_stale_reviews": True,
                    "require_code_owner_reviews": False,
                    "required_approving_review_count": args.review_count,
                }
                if not args.no_pr_reviews
                else None
            ),
            "restrictions": {"users": [], "teams": []},
            "allow_force_pushes": False,
            "allow_deletions": False,
            "required_linear_history": True,
        }
        print(json.dumps(preview_config, indent=2, ensure_ascii=False))
        print()
        if not token:
            print("⚠️ 未设置 GITHUB_TOKEN，以上仅为参数预览。")
            print("   设置 Token 后可实际运行以应用配置：")
            print("   set GITHUB_TOKEN=ghp_xxxxxxxxxxxx")
            print(f"   python scripts/configure_branch_protection.py --repo {repo}")
        return 0

    # 配置分支保护
    print("─── 配置分支保护规则 ───")
    try:
        configure_branch_protection(
            repo=repo,
            branch=args.branch,
            token=token,
            required_checks=all_checks,
            require_pr_reviews=not args.no_pr_reviews,
            required_approving_review_count=args.review_count,
        )
        print("✅ 分支保护规则配置成功")
    except RuntimeError as e:
        print(f"❌ 配置失败: {e}")
        return 1

    print()

    # 验证配置
    print("─── 验证配置 ───")
    if verify_protection(repo, args.branch, token, all_checks):
        print()
        print("🎉 分支保护规则已生效！")
        print()
        print("📋 配置摘要:")
        print(f"  • 必需状态检查: {all_checks}")
        print(f"  • 要求 PR 审批: {'是' if not args.no_pr_reviews else '否'}")
        if not args.no_pr_reviews:
            print(f"  • 审批人数: {args.review_count}")
        print(f"  • 强制管理员遵守: 是")
        print(f"  • 禁止 force push: 是")
        print(f"  • 禁止删除分支: 是")
        print(f"  • 要求线性历史: 是")
        return 0
    else:
        print("❌ 验证失败，请检查 GitHub 设置页面")
        return 1


if __name__ == "__main__":
    sys.exit(main())
