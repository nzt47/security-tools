# -*- coding: utf-8 -*-
"""根据 Release Notes 后续计划在 Jira 创建任务（KN-101 ~ KN-105）。

用法：
    python scripts/create_jira_tasks.py --dry-run            # 预览（不请求）
    python scripts/create_jira_tasks.py --project KN         # 创建到项目 KN
    python scripts/create_jira_tasks.py --project KN --type "技术任务"

前置条件（环境变量，遵循项目 .env 约定）：
    JIRA_BASE_URL=https://your-jira.example.com
    JIRA_USER=your-username
    JIRA_TOKEN=your-api-token          # Jira API Token（非密码）

幂等：按 summary 精确匹配已存在任务，存在则跳过（可重复运行）。

依赖：requests（已在 requirements 中）。
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

# Release Notes §6 后续计划（与 docs/zh/知识模块性能优化_Jira任务清单.md 对齐）
TASKS: list[dict] = [
    {
        "key": "KN-101",
        "summary": "[知识模块] 流式扫描落地（iter_cards）",
        "priority": "High",
        "component": "knowledge",
        "description": (
            "实现 iter_cards() 生成器（逐卡扫描，免全库 Card 对象驻留），"
            "重构 rebuild_links_index 全量重建与 delete_many 回退全扫为流式读取；"
            "内存 O(N) → O(U)。\n"
            "DoD：\n"
            "1) iter_cards() 单次驻留 ≤1 张 + refs 映射；\n"
            "2) 重建/回退路径判定结果与 list() 版本逐字节一致；\n"
            "3) 10 万卡基准内存峰值下降 ≥10x；\n"
            "4) links_index 11 项 + 回归 70+ 全绿。\n"
            "参考：docs/zh/知识模块性能优化_流式扫描优化方案草稿.md"
        ),
    },
    {
        "key": "KN-102",
        "summary": "[知识模块] 容量监控埋点落地",
        "priority": "High",
        "component": "knowledge",
        "description": (
            "落实容量监控：refs > 512 MB（≈400 万卡）触发分片评估告警；"
            "文件数 > 1000 万触发存储层改造告警；阈值经 .env 配置。\n"
            "DoD：\n"
            "1) read_links_index / rebuild_links_index 解析后统计 refs 条目数并输出 info；\n"
            "2) 超阈值输出可检索 WARNING（含估算/文件数/阈值）；\n"
            "3) 阈值经 KNOWLEDGE_LINKS_REFS_ALERT_MB / KNOWLEDGE_CARD_FILE_ALERT_COUNT；\n"
            "4) 新增 ≥8 项单测，含零副作用锁定用例；回归全绿。\n"
            "参考：docs/zh/知识模块性能优化_监控埋点实现方案草案.md"
        ),
    },
    {
        "key": "KN-103",
        "summary": "[知识模块] 分片架构前置设计",
        "priority": "Medium",
        "component": "knowledge",
        "description": (
            "≥500 万卡前置准备：分片路由（hash(ref_slug) % N）、跨片一致性（补偿/重放/对账）、"
            "重建并行化。片数基线：64/2000 万、256/5000 万、512/1 亿、4096/10 亿。\n"
            "DoD：\n"
            "1) 输出分片设计文档（路由/写路径/读路径/对账/不变量改写）；\n"
            "2) 明确与 KN-101 的关系（流式为分片基础形态）；\n"
            "3) 评审通过后归档。\n"
            "依赖：KN-101。"
        ),
    },
    {
        "key": "KN-104",
        "summary": "[知识模块] 存储层二级目录/对象存储评估",
        "priority": "Low",
        "component": "knowledge",
        "description": (
            "≥5000 万卡文件数瓶颈评估：二级目录分层（<type>/<前缀>/<slug>.md）、对象存储"
            "（分桶）可行性、遍历基准重新标定（0.225 ms/卡 在 5000 万文件规模失效）。\n"
            "DoD：\n"
            "1) 输出存储层改造评估报告（成本/性能/迁移/回滚）；\n"
            "2) 阈值建议（文件数 > 1000 万触发，与 KN-102 联动）。"
        ),
    },
    {
        "key": "KN-105",
        "summary": "[知识模块] Confluence 推送（缺凭据）",
        "priority": "Low",
        "component": "knowledge",
        "description": (
            "将《第二批对比报告》《架构演进总结》推送至 Confluence。\n"
            "缺凭据（CONFLUENCE_BASE_URL / CONFLUENCE_USER / CONFLUENCE_TOKEN），"
            "待提供后执行；备选：团队 Wiki 已覆盖（docs/wiki/）。\n"
            "DoD：两页面成功创建（Space 确认 + 链接回填）。"
        ),
    },
]


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}（请在 .env 或环境变量中配置）")
    return value


def _find_existing(session: requests.Session, base: str, project: str, summary: str) -> bool:
    """按 summary 精确匹配（JQL），存在返回 True（幂等跳过）。"""
    url = f"{base}/rest/api/2/search"
    jql = f'project = "{project}" AND summary ~ "\\"{summary}\\""'
    resp = session.get(url, params={"jql": jql, "maxResults": 5}, timeout=30)
    resp.raise_for_status()
    total = resp.json().get("total", 0)
    if total:
        issues = resp.json().get("issues", [])
        for issue in issues:
            if issue["fields"].get("summary") == summary:
                return True
    return False


def create_task(
    session: requests.Session, base: str, project: str, issue_type: str, task: dict
) -> str:
    payload = {
        "fields": {
            "project": {"key": project},
            "summary": task["summary"],
            "issuetype": {"name": issue_type},
            "priority": {"name": task["priority"]},
            "description": task["description"],
            "components": [{"name": task["component"]}],
        }
    }
    resp = session.post(f"{base}/rest/api/2/issue", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("key", "?")


def main() -> int:
    parser = argparse.ArgumentParser(description="创建知识模块后续任务（KN-101~105）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不发请求")
    parser.add_argument("--project", default=os.environ.get("JIRA_PROJECT", "KN"), help="Jira 项目 key")
    parser.add_argument("--type", default="技术任务", help="issue 类型名称")
    args = parser.parse_args()

    if args.dry_run:
        for t in TASKS:
            print(f"[dry-run] 将创建: {t['key']} | {t['summary']} | 优先级={t['priority']} | 组件={t['component']}")
        print(f"[dry-run] 共 {len(TASKS)} 个任务（--project={args.project} --type={args.type}）")
        return 0

    base = _env("JIRA_BASE_URL").rstrip("/")
    user = _env("JIRA_USER")
    token = _env("JIRA_TOKEN")
    session = requests.Session()
    session.auth = (user, token)
    session.headers["Accept"] = "application/json"

    created, skipped = 0, 0
    for t in TASKS:
        if _find_existing(session, base, args.project, t["summary"]):
            print(f"[skip] 已存在: {t['key']} | {t['summary']}")
            skipped += 1
            continue
        key = create_task(session, base, args.project, args.type, t)
        print(f"[created] {t['key']} → {key} | {t['summary']}")
        created += 1
    print(f"完成: 创建 {created} 个，跳过（已存在）{skipped} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
