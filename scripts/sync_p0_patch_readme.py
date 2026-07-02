#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Confluence 同步包装脚本 — P0 安全修复补丁包 README

将 patches/p0_security/README.md 同步到团队 Confluence 知识库。
本脚本是 scripts/sync_to_confluence.py 的包装器，预设了 P0 补丁包的参数。

使用前配置环境变量：
    CONFLUENCE_BASE_URL=https://your-team.atlassian.net/wiki
    CONFLUENCE_USER=your-email@team.com
    CONFLUENCE_TOKEN=your-api-token

获取 API Token：
    https://id.atlassian.com/manage-profile/security/api-tokens

运行方式：
    python scripts/sync_p0_patch_readme.py
    python scripts/sync_p0_patch_readme.py --space "SEC"
    python scripts/sync_p0_patch_readme.py --page-id 123456789  # 更新已有页面
    python scripts/sync_p0_patch_readme.py --dry-run            # 仅打印命令
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="同步 P0 安全修复补丁包 README 到 Confluence"
    )
    parser.add_argument(
        "--space", default="SEC",
        help="Confluence Space Key（默认 SEC）"
    )
    parser.add_argument(
        "--title", default="P0 安全修复补丁包说明",
        help="页面标题（默认 'P0 安全修复补丁包说明'）"
    )
    parser.add_argument(
        "--page-id", type=int, default=None,
        help="已有页面 ID（更新而非创建新页面）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅打印命令，不实际执行"
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    readme_path = project_root / "patches" / "p0_security" / "README.md"

    print("=" * 60)
    print("P0 安全修复补丁包 README -> Confluence 同步")
    print("=" * 60)
    print(f"项目根目录: {project_root}")
    print(f"Space: {args.space}")
    print(f"Title: {args.title}")
    if args.page_id:
        print(f"PageId: {args.page_id} (更新模式)")
    print()

    # 检查 README 文件
    if not readme_path.exists():
        print(f"错误: README 文件不存在: {readme_path}")
        sys.exit(1)

    file_size = readme_path.stat().st_size
    print(f"README 文件: {readme_path.name} ({file_size} 字节)")
    print()

    # 检查环境变量
    base_url = os.getenv("CONFLUENCE_BASE_URL")
    user = os.getenv("CONFLUENCE_USER")
    token = os.getenv("CONFLUENCE_TOKEN")

    if not all([base_url, user, token]):
        print("错误: Confluence 环境变量未设置")
        print()
        print("请设置以下环境变量后重试：")
        print('  set CONFLUENCE_BASE_URL=https://your-team.atlassian.net/wiki')
        print('  set CONFLUENCE_USER=your-email@team.com')
        print('  set CONFLUENCE_TOKEN=your-api-token')
        print()
        print("获取 API Token: https://id.atlassian.com/manage-profile/security/api-tokens")
        print()
        print("PowerShell 永久设置：")
        print('  [Environment]::SetEnvironmentVariable("CONFLUENCE_BASE_URL", "https://your-team.atlassian.net/wiki", "User")')
        print('  [Environment]::SetEnvironmentVariable("CONFLUENCE_USER", "your-email@team.com", "User")')
        print('  [Environment]::SetEnvironmentVariable("CONFLUENCE_TOKEN", "your-api-token", "User")')
        print()

        if args.dry_run:
            print("[DryRun] 环境变量未设置，设置后将执行：")
            cmd = _build_cmd(args, readme_path)
            print(f"  {' '.join(cmd)}")
            sys.exit(0)
        sys.exit(1)

    print(f"Confluence Base URL: {base_url}")
    print(f"User: {user}")
    print(f"Token: {'*' * len(token)} (已设置)")
    print()

    # 构建命令
    cmd = _build_cmd(args, readme_path)

    if args.dry_run:
        print("[DryRun] 将执行以下命令：")
        print(f"  {' '.join(cmd)}")
        print()
        print("环境变量已就绪，移除 --dry-run 参数即可实际执行同步。")
        sys.exit(0)

    # 执行同步
    print("开始同步到 Confluence...")
    print()
    result = subprocess.run(cmd, cwd=str(project_root))

    if result.returncode == 0:
        print()
        print("同步成功！页面已创建/更新到 Confluence。")
        print()
        print("后续步骤：")
        print("  1. 访问 Confluence 确认页面内容渲染正确")
        print("  2. 如需更新已有页面，使用 --page-id 参数：")
        print(f"     python {Path(__file__).name} --page-id <页面ID>")
    else:
        print()
        print(f"同步失败！退出码: {result.returncode}")
        print()
        print("常见问题：")
        print(f"  1. 网络连接：确认能访问 {base_url}")
        print("  2. 凭据错误：检查 CONFLUENCE_USER 和 CONFLUENCE_TOKEN")
        print(f"  3. Space Key 错误：确认 Space '{args.space}' 存在")
        print(f"  4. 权限不足：确认账号有 Space '{args.space}' 的写入权限")

    sys.exit(result.returncode)


def _build_cmd(args, readme_path):
    """构建同步命令"""
    cmd = [
        sys.executable, "scripts/sync_to_confluence.py",
        "--space", args.space,
        "--title", args.title,
        "--report-path", "patches/p0_security/README.md",
    ]
    if args.page_id:
        cmd.extend(["--page-id", str(args.page_id)])
    return cmd


if __name__ == "__main__":
    main()
