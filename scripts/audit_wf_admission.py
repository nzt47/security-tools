# -*- coding: utf-8 -*-
"""工作流准入审计（只读，TASK-S10-01）— 可复算的存量读数与匹配面实测

产出两组**可复算**数字：

A. 准入读数（判定源 = `agent.workflow_learning.admission`，与运行时同一份）
   - 阈值、条目总数、匹配候选数、按拒绝码分布
   - 单字触发词条目清单 / 单步条目清单 / 单会话（单轮来源）清单
   - 可自动升格为 Skill 的条目清单

B. 匹配面实测（用真实 `WorkflowMatcher` 逐个单独注册）
   - 探针语料 = 仓库内各工作流的 `source_user_input`（跨任务）+ 固定通用请求
   - 对每条工作流输出：命中数、最高相似度、命中项

用法：
    python scripts/audit_wf_admission.py
    python scripts/audit_wf_admission.py --repo data/learned_workflows.json
    python scripts/audit_wf_admission.py --json > evidence.json
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.workflow_learning.admission import (  # noqa: E402
    MIN_CROSS_SESSION_SUPPORT, MIN_STEPS, MIN_TRIGGER_CHARS,
)
from agent.workflow_learning.matcher import WorkflowMatcher  # noqa: E402
from agent.workflow_learning.models import LearnedWorkflow  # noqa: E402
from agent.workflow_learning.repository import WorkflowRepository  # noqa: E402
from agent.workflow_learning.service import WorkflowLearningService  # noqa: E402

#: 固定通用请求探针（含任务书点名的"1+1 等于几"诱因场景）
GENERIC_PROBES = [
    "1+1 等于几",
    "今天天气怎么样",
    "帮我写一份工作总结",
    "介绍一下当前的项目结构",
    "请列出当前的状态",
    "现在几点了",
    "把这段话翻译成英文",
    "总结一下这篇文档",
    "当前工作进展如何",
    "帮我查一下这个错误",
    "打印文件内容",
    "这个功能怎么用",
]

#: 跨任务短探针（含单字触发词的工作流最容易被这类短问命中——
#: 索引构造含 source_user_input，长问句会因 IDF 归一化把相似度压到 ≈0）
SHORT_CROSS_TASK_PROBES = [
    "列出文件",
    "当前状态",
    "统计一下",
    "打包代码",
    "读取配置",
]


def _probe_queries(repo_path: str) -> list:
    raw = json.load(io.open(repo_path, encoding="utf-8"))
    stock = sorted({v.get("source_user_input", "") for v in raw.values()})
    return [p for p in stock + GENERIC_PROBES + SHORT_CROSS_TASK_PROBES if p]


def probe_surface(repo_path: str) -> dict:
    """**全库注册**后测真实匹配面（与运行时一致），逐条单独注册的读数另见
    `per_workflow_solo`"""
    raw = json.load(io.open(repo_path, encoding="utf-8"))
    probes = _probe_queries(repo_path)
    m = WorkflowMatcher()
    registered = []
    for wf_id, data in sorted(raw.items()):
        if m.register(LearnedWorkflow(**data)):
            registered.append(wf_id)

    hits = []
    for q in probes:
        for wf, score in m.match(q, top_k=5):
            hits.append({"query": q, "workflow_id": wf.id,
                         "combined_score": round(score, 4)})
    per_workflow = {}
    for wf_id, data in sorted(raw.items()):
        mine = [h for h in hits if h["workflow_id"] == wf_id]
        per_workflow[wf_id] = {
            "step_count": len(data.get("steps") or []),
            "trigger_patterns": data.get("trigger_patterns") or [],
            "status": data.get("status"),
            "match_candidate": wf_id in registered,
            "hit_count": len(mine),
            "hits": mine,
        }
    return {
        "probe_count": len(probes),
        "probes": probes,
        "registered": registered,
        "total_hits": len(hits),
        "query_hit_count": len({h["query"] for h in hits}),
        "hits": hits,
        "per_workflow": per_workflow,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="工作流准入审计（只读）")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.json:
        sys.stdout.reconfigure(encoding="utf-8")

    repo = WorkflowRepository(path=args.repo)
    report = WorkflowLearningService(repo_path=str(repo._path)).admission_report()
    surface = probe_surface(str(repo._path))

    payload = {
        "thresholds": {
            "MIN_STEPS": MIN_STEPS,
            "MIN_TRIGGER_CHARS": MIN_TRIGGER_CHARS,
            "MIN_CROSS_SESSION_SUPPORT": MIN_CROSS_SESSION_SUPPORT,
        },
        "admission_report": report,
        "match_surface": surface,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print("A. 准入读数")
    print("=" * 78)
    print(f"仓库: {repo._path}")
    print(f"阈值: MIN_STEPS={MIN_STEPS} MIN_TRIGGER_CHARS={MIN_TRIGGER_CHARS} "
          f"MIN_CROSS_SESSION_SUPPORT={MIN_CROSS_SESSION_SUPPORT}")
    print(f"条目总数: {report['total']}   匹配候选: {report['match_candidates']}")
    print(f"拒绝码分布: {report['rejected_by_code']}")
    print(f"单字触发词条目({len(report['single_char_trigger_workflows'])}): "
          f"{[x['workflow_id'] for x in report['single_char_trigger_workflows']]}")
    print(f"单步条目({len(report['single_step_workflows'])}): "
          f"{report['single_step_workflows']}")
    print(f"单会话/单轮来源条目({len(report['single_session_workflows'])}): "
          f"{[(x['workflow_id'], x['support_sessions']) for x in report['single_session_workflows']]}")
    print(f"可自动升格为 Skill: {report['convertible']}")
    print()
    print("=" * 78)
    print("B. 匹配面实测（**全库注册**，与运行时一致）")
    print("=" * 78)
    print(f"入索引的工作流: {surface['registered']}")
    print(f"探针 {surface['probe_count']} 条：命中查询 {surface['query_hit_count']} 条、"
          f"候选对 {surface['total_hits']} 个")
    if surface["hits"]:
        for h in surface["hits"]:
            print(f"    HIT {h['query']!r} -> {h['workflow_id']} "
                  f"(combined={h['combined_score']})")
    else:
        print("    （无任何命中）")
    print("-" * 78)
    for wf_id, s in surface["per_workflow"].items():
        print(f"{wf_id} | steps={s['step_count']} | status={s['status']} | "
              f"候选={'是' if s['match_candidate'] else '否'} | 命中={s['hit_count']}")
        print(f"    触发词={s['trigger_patterns']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
