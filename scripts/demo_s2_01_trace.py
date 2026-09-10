#!/usr/bin/env python3
"""TASK-S2-01 统一 Trace 端到端演示 + 台账摘要输出

演示（对齐任务书 §二 步骤 2 验证要求）：

  1. 一个「修复失败测试」任务 → 任务级主 Trace（task 级）
  2. 三个工具级子 Trace（read_file / shell_execute / write_file）
  3. parent_trace_id 链还原（主 → 子）
  4. 全程含 workspace_id（P7.1-19）
  5. trace.capability_id ↔ descriptors 台账 join（S1-01 遗留 #3 运行时接线）
  6. 输出 data/trace_stats.json 摘要（S3 判定集 / S5 评测数据源声明）

用法：
    python scripts/demo_s2_01_trace.py                 # 写 data/trace_v2_demo.db + data/trace_stats.json
    python scripts/demo_s2_01_trace.py --db <path>     # 指定台账
    python scripts/demo_s2_01_trace.py --json          # 只输出 JSON 摘要
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.observability.trace_v2 import (  # noqa: E402
    ACTOR_AUTO,
    ACTOR_HUMAN,
    ACTOR_SUB_AGENT,
    STATUS_SUCCESS,
    SideEffects,
    TraceFacade,
    derive_workspace_id,
    load_runtime_descriptors,
)

_DEFAULT_DB = os.path.join(_ROOT, "data", "trace_v2_demo.db")
_DEFAULT_STATS = os.path.join(_ROOT, "data", "trace_stats.json")

# 演示任务的工作区（workspace-hash → workspace_id，P7.1-19）
_DEMO_WORKSPACE_ROOT = os.path.join(_ROOT, "workspace", "demo-repo")


def _demo_tools() -> list:
    """演示用工具清单（与内置工具桥接同形：name/description）"""
    return [
        {"name": "read_file", "description": "读取文件内容"},
        {"name": "shell_execute", "description": "执行 shell 命令"},
        {"name": "write_file", "description": "写入文件"},
    ]


def run_demo(db_path: str, stats_path: str) -> dict:
    facade = TraceFacade(db_path)
    workspace_id = derive_workspace_id(_DEMO_WORKSPACE_ROOT)

    # ① 任务级主 Trace（task 级；P7.2-08：tenant_id = workspace-hash）
    root = facade.start(
        task_id="fix-failed-test-demo",
        tenant_id=workspace_id,
        workspace_id=workspace_id,
        subject_id="demo-user",
        policy_version="policy-v1",
    )

    # ② 工具级子 Trace（actor 覆盖 human/auto/sub_agent 三态）
    # 注入的占位密钥用 sk-test- 前缀：命中 .github/gitleaks-config.toml 的占位符白名单
    # （^sk-(test|secret|real|instance)...，非真实密钥），用途＝验证 redact→hash→持久化
    # 顺序下原文不可恢复。
    facade.record(
        "cp.builtin.read_file",
        args={"path": "tests/test_demo.py",
              "api_key": "sk-test-DEMO-SHOULD-BE-REDACTED"},
        output={"ok": True, "content": "def test_demo(): assert 1 == 2"},
        actor=ACTOR_HUMAN,
        input_tokens=120, output_tokens=40, cost_usd=0.0009,
        side_effects=SideEffects(notes=["读取失败测试用例"]),
    )
    facade.record(
        "cp.builtin.shell_execute",
        args={"cmd": "pytest tests/test_demo.py -q"},
        output={"ok": False, "error": "1 failed in 0.12s"},
        actor=ACTOR_AUTO,
        input_tokens=0, output_tokens=80, cost_usd=0.0002,
        side_effects=SideEffects(external_calls=["subprocess:pytest"]),
    )
    facade.record(
        "cp.builtin.write_file",
        args={"path": "src/demo.py", "content": "def test_demo(): assert 1 == 1"},
        output={"ok": True},
        actor=ACTOR_SUB_AGENT,
        cost_usd=0.0001,
        side_effects=SideEffects(files_written=["src/demo.py"]),
    )
    task_trace = facade.finish(status=STATUS_SUCCESS)
    facade.flush()

    # ③ parent 链还原
    chain = facade.chain(root)
    # ④ 任务聚合
    summary = facade.task_summary("fix-failed-test-demo")
    # ⑤ capability ↔ descriptor join（运行时真实装载内置工具）
    registry, load_summary = load_runtime_descriptors(builtin_entries=_demo_tools())
    join_rows = []
    for trace in chain:
        if not trace.capability_id:
            continue
        desc = registry.get(trace.capability_id)
        join_rows.append({
            "capability_id": trace.capability_id,
            "joined": desc is not None,
            "source_type": desc.origin.source_type.value if desc else None,
            "provenance": desc.origin.provenance.value if desc else None,
        })
    # ⑥ 摘要输出
    stats = facade.write_stats(stats_path)

    return {
        "ok": True,
        "workspace_id": workspace_id,
        "task_trace_id": root,
        "task_trace_status": task_trace.response.status if task_trace else None,
        "chain": [
            {
                "trace_id": t.trace_id,
                "parent_trace_id": t.parent_trace_id,
                "capability_id": t.capability_id or "(task)",
                "actor": t.actor,
                "status": t.response.status,
                "workspace_id": t.tenancy.workspace_id,
                "duration_ms": round(t.timing.duration_ms or 0, 3),
            }
            for t in chain
        ],
        "chain_parent_links_ok": all(
            t.parent_trace_id == root for t in chain if t.capability_id),
        "all_have_workspace_id": all(t.tenancy.workspace_id for t in chain),
        "task_summary": summary,
        "descriptor_load": load_summary,
        "capability_join": join_rows,
        "stats": stats,
        "stats_path": stats_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-S2-01 统一 Trace 端到端演示")
    parser.add_argument("--db", default=_DEFAULT_DB, help="统一 Trace 台账路径")
    parser.add_argument("--stats", default=_DEFAULT_STATS, help="摘要输出路径")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    args = parser.parse_args()

    result = run_demo(args.db, args.stats)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print("TASK-S2-01 统一 Trace 端到端演示（§3.4 规格 + §2.7 TraceContext 透传）")
    print("=" * 68)
    print(f"台账：{args.db}")
    print(f"workspace_id（workspace-hash，P7.1-19）：{result['workspace_id']}")
    print(f"任务主 Trace：{result['task_trace_id']}（{result['task_trace_status']}）")
    print("\nparent 链（主 → 子）：")
    for row in result["chain"]:
        print(f"  - {row['trace_id']}  parent={row['parent_trace_id'] or '(root)'}  "
              f"{row['capability_id']:<28} actor={row['actor']:<9} "
              f"status={row['status']:<7} ws={row['workspace_id']}  "
              f"{row['duration_ms']}ms")
    print(f"\nparent 链全部指向主 Trace：{result['chain_parent_links_ok']}")
    print(f"全程含 workspace_id：{result['all_have_workspace_id']}")
    print(f"任务聚合：{json.dumps(result['task_summary'], ensure_ascii=False)}")
    print("\ncapability ↔ descriptor join（S1-01 遗留 #3）：")
    for row in result["capability_join"]:
        print(f"  - {row['capability_id']:<28} joined={row['joined']} "
              f"source={row['source_type']} provenance={row['provenance']}")
    print(f"\n台账摘要已写出：{result['stats_path']}")
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
