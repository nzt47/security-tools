# -*- coding: utf-8 -*-
"""首批内置工作流模板（2026-11-13，零 token）。

【为什么】单位看板 `workflows.store` 长期是 **never「尚无工作流（0 条）」**：
系统一直没有"第一批工作流"的种子，用户打开页面只看到空表。口径上 never 不计分，
所以它不是"故障"，但确实是产品缺口。本模块给出一批**只读、可审阅**的模板，
让仓库有第一批条目、页面有可参考的结构。

【口径（改之前先读）】
1. **模板默认不启用**：`enabled=False` + `status="draft"`。落库 ≠ 上线 ——
   启用与否是人的决定（工作流回放会真的执行工具）。
2. **只用只读工具**：list_directory / search_files / grep / run_lint / search_memory，
   effect=read 或低风险静态检查；不写文件、不删东西、不联网。
3. **幂等、只补缺**：按 id upsert 前先 `repo.get(id)`；已存在（含用户改过的同 id）
   **一律跳过**，绝不覆盖。删除/改名仍由人来。
4. **不自动运行**：模板没有 session 来源、observed_count=1、priority 低；
   匹配器对 draft/disabled 条目不会启用（`repository.list_all(enabled_only=True)`
   同时要求 enabled 与 status=active）。
5. **零 token**：本模块只写本地仓库文件，不调 LLM。

用法：
    python -m agent.workflow_learning.templates --seed --json
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional

from .models import LearnedWorkflow, WorkflowStatus
from .repository import WorkflowRepository

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 模板统一标签（便于按来源过滤；不参与匹配权重）
TEMPLATE_TAGS = ("template", "builtin", "readonly")


def _tpl(wf_id: str, name: str, description: str, signature: str,
         triggers: List[str], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构造一条模板定义（字段集中在这里，少写重复）。"""
    return {
        "id": wf_id,
        "name": name,
        "description": description,
        "task_signature": signature,
        "trigger_patterns": list(triggers),
        "steps": steps,
        "tags": list(TEMPLATE_TAGS),
        "workflow_type": "toolchain",
        "status": WorkflowStatus.DRAFT.value,
        "enabled": False,
        "priority": 10,
        "confidence": 0.5,
        "source_session_id": "builtin:template",
        "source_user_input": "",
    }


#: 首批模板（4 条；全部只读）
BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    _tpl(
        "tpl-repo-overview",
        "模板·仓库结构总览",
        "列出仓库根目录与 docs 下的 Markdown 清单，作为「接手/交接」的第一屏素材（只读）。",
        "查看仓库结构与文档清单",
        ["仓库结构", "目录总览", "文档清单"],
        [
            {"step_id": "list-root", "tool_name": "list_directory",
             "params_template": {"path": ".", "show_hidden": False},
             "output_key": "root", "description": "根目录一级条目"},
            {"step_id": "search-docs", "tool_name": "search_files",
             "params_template": {"pattern": "*.md", "root_path": "docs"},
             "output_key": "docs", "description": "docs 下的 Markdown 文件"},
        ],
    ),
    _tpl(
        "tpl-todo-scan",
        "模板·待办与断点扫描",
        "在 agent/ 下检索 TODO / FIXME / XXX，作为维护排期的输入（只读、零 token）。",
        "扫描代码里的 TODO 与 FIXME",
        ["扫描待办", "TODO", "FIXME"],
        [
            {"step_id": "grep-todos", "tool_name": "grep",
             "params_template": {"pattern": "TODO|FIXME|XXX", "path": "agent",
                                 "include": "*.py"},
             "output_key": "todos", "description": "命中的行（路径:行号 + 原文）"},
        ],
    ),
    _tpl(
        "tpl-lint-check",
        "模板·静态检查",
        "对仓库跑一次静态检查（ruff 优先，缺失退回 mypy），只收集计数与输出尾部。",
        "跑一次静态检查",
        ["静态检查", "lint", "ruff", "mypy"],
        [
            {"step_id": "lint", "tool_name": "run_lint",
             "params_template": {"path": ".", "tool": "auto"},
             "output_key": "lint", "description": "error_count / warning_count / 输出尾部"},
        ],
    ),
    _tpl(
        "tpl-memory-recall",
        "模板·记忆回溯",
        "按用户输入检索既有记忆（语义 + 事件日志），给讨论提供上下文（只读）。",
        "按关键词检索记忆",
        ["检索记忆", "回忆一下", "记忆里有没有"],
        [
            {"step_id": "recall", "tool_name": "search_memory",
             "params_template": {"query": "$input", "scope": "all", "max_items": 10},
             "output_key": "hits", "description": "命中的记忆条目"},
        ],
    ),
]


def seed_templates(repo_path: Optional[str] = None) -> Dict[str, Any]:
    """把模板补进仓库（**只补缺、不覆盖**）；返回计数，永不抛。"""
    try:
        repo = WorkflowRepository(str(repo_path) if repo_path else None)
    except Exception as exc:  # noqa: BLE001 仓库不可重建时如实回报
        logger.warning("[wf-templates] 仓库打开失败（%s）", type(exc).__name__)
        return {"ok": False, "added": [], "skipped": [], "total": None,
                "reason": "repo_unavailable"}
    added: List[str] = []
    skipped: List[str] = []
    for raw in BUILTIN_TEMPLATES:
        try:
            wf = LearnedWorkflow(**raw)  # 先校验，坏模板不落库
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wf-templates] 模板校验失败（%s）", type(exc).__name__)
            skipped.append(str(raw.get("id")))
            continue
        if repo.get(wf.id) is not None:
            skipped.append(wf.id)
            continue
        try:
            repo.upsert(wf)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wf-templates] 模板写入失败（%s）", type(exc).__name__)
            skipped.append(wf.id)
            continue
        added.append(wf.id)
    try:
        total = repo.count()
    except Exception:  # noqa: BLE001
        total = None
    return {"ok": True, "added": added, "skipped": skipped, "total": total}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="补种首批内置工作流模板（幂等、只补缺）")
    parser.add_argument("--seed", action="store_true", help="执行补种")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    if not args.seed:
        for item in BUILTIN_TEMPLATES:
            print("%-22s %-28s steps=%d" % (item["id"], item["name"], len(item["steps"])))
        return 0
    result = seed_templates()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("模板补种：新增 %d / 跳过 %d / 仓库共 %s 条"
              % (len(result["added"]), len(result["skipped"]), result["total"]))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
