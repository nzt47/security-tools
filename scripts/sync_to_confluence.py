#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Confluence 知识库同步脚本

将 P0 安全修复复盘报告同步到团队 Confluence 知识库。

使用前配置：
    1. 设置环境变量：
       export CONFLUENCE_BASE_URL="https://your-team.atlassian.net/wiki"
       export CONFLUENCE_USER="your-email@team.com"
       export CONFLUENCE_TOKEN="your-api-token"

    2. 安装依赖：
       pip install requests

    3. 运行：
       python scripts/sync_to_confluence.py --space "SEC" --title "P0 安全修复复盘报告"

    4. 更新已有页面（而非创建新页面）：
       python scripts/sync_to_confluence.py --space "SEC" --page-id 123456789

获取 API Token：
    https://id.atlassian.com/manage-profile/security/api-tokens
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("错误: 请先安装 requests: pip install requests")
    sys.exit(1)


def markdown_to_confluence_storage(md_text: str) -> str:
    """将 Markdown 转换为 Confluence Storage Format（简化版）

    Confluence 支持直接插入 Markdown，但以下转换可改善显示效果：
    - ```code``` → <ac:structured-macro> 代码块
    - | table | → Confluence 表格
    """
    # Confluence 支持直接粘贴 Markdown，这里做简单转换
    # 将代码块转换为 Confluence 代码宏
    import re

    # 转换代码块
    def code_block_replacer(m):
        lang = m.group(1) or "none"
        code = m.group(2).replace("<", "&lt;").replace(">", "&gt;")
        return (
            f'<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
            f'<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>'
            f'</ac:structured-macro>'
        )

    result = re.sub(
        r'```(\w*)\n(.*?)```',
        code_block_replacer,
        md_text,
        flags=re.DOTALL,
    )
    return result


def get_page_id(base_url: str, auth: tuple, space_key: str, title: str) -> int:
    """查询 Confluence 页面 ID"""
    url = f"{base_url}/rest/api/content"
    params = {
        "spaceKey": space_key,
        "title": title,
        "expand": "version",
    }
    resp = requests.get(url, auth=auth, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    if results:
        return results[0]
    return None


def create_page(base_url: str, auth: tuple, space_key: str, title: str, content: str) -> dict:
    """创建新页面"""
    url = f"{base_url}/rest/api/content"
    payload = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": {
            "storage": {
                "value": content,
                "representation": "storage",
            }
        },
    }
    resp = requests.post(url, auth=auth, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_page(base_url: str, auth: tuple, page_id: int, title: str, content: str, version: int) -> dict:
    """更新已有页面"""
    url = f"{base_url}/rest/api/content/{page_id}"
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "body": {
            "storage": {
                "value": content,
                "representation": "storage",
            }
        },
        "version": {"number": version + 1},
    }
    resp = requests.put(url, auth=auth, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="同步复盘报告到 Confluence")
    parser.add_argument("--space", required=True, help="Confluence Space Key")
    parser.add_argument("--title", default="P0 安全修复复盘报告", help="页面标题")
    parser.add_argument("--page-id", type=int, help="已有页面 ID（更新而非创建）")
    parser.add_argument("--report-path", default="docs/security/p0_security_retrospective.md",
                        help="复盘报告文件路径")
    args = parser.parse_args()

    # 读取凭据
    base_url = os.getenv("CONFLUENCE_BASE_URL")
    user = os.getenv("CONFLUENCE_USER")
    token = os.getenv("CONFLUENCE_TOKEN")

    if not all([base_url, user, token]):
        print("错误: 请设置环境变量 CONFLUENCE_BASE_URL, CONFLUENCE_USER, CONFLUENCE_TOKEN")
        print("获取 Token: https://id.atlassian.com/manage-profile/security/api-tokens")
        sys.exit(1)

    auth = (user, token)

    # 读取报告
    report_path = Path(__file__).parent.parent / args.report_path
    if not report_path.exists():
        print(f"错误: 报告文件不存在: {report_path}")
        sys.exit(1)

    md_content = report_path.read_text(encoding="utf-8")
    confluence_content = markdown_to_confluence_storage(md_content)

    print(f"报告路径: {report_path}")
    print(f"Confluence: {base_url}")
    print(f"Space: {args.space}")
    print(f"标题: {args.title}")
    print(f"内容长度: {len(confluence_content)} 字符")
    print("-" * 60)

    try:
        if args.page_id:
            # 更新已有页面
            print(f"更新页面 ID: {args.page_id}")
            # 先获取当前版本号
            existing = get_page_id(base_url, auth, args.space, args.title)
            if existing:
                version = existing.get("version", {}).get("number", 0)
            else:
                version = 0
            result = update_page(base_url, auth, args.page_id, args.title, confluence_content, version)
            print(f"✅ 页面已更新: {base_url}{result.get('_links', {}).get('webui', '')}")
        else:
            # 检查页面是否已存在
            existing = get_page_id(base_url, auth, args.space, args.title)
            if existing:
                page_id = existing["id"]
                version = existing.get("version", {}).get("number", 0)
                print(f"页面已存在 (ID: {page_id})，执行更新...")
                result = update_page(base_url, auth, page_id, args.title, confluence_content, version)
                print(f"✅ 页面已更新: {base_url}{result.get('_links', {}).get('webui', '')}")
            else:
                print("创建新页面...")
                result = create_page(base_url, auth, args.space, args.title, confluence_content)
                print(f"✅ 页面已创建: {base_url}{result.get('_links', {}).get('webui', '')}")
    except requests.HTTPError as e:
        print(f"❌ HTTP 错误: {e}")
        print(f"响应: {e.response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
