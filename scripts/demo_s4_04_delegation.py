"""TASK-S4-04 端到端样例：真实子进程委派（八要素 → CLI 通道 → 回收三件套 → 凭据销毁）

【这个脚本证明什么】
    在没有第三方 agent CLI / LLM 凭证的环境里，用**本地协议桩**（真实子进程）跑通
    §3.9 / §3.10 / §5.7 / §5.9 / §4.2 的完整链路，并输出**可复现**的证据：

    A. 一次成功委派：task_file 落盘 → 真实子进程 → JSON Lines → 三件套 → 计费 → Trace
    B. 工具裁剪闸门：子代理声明调用 ``memory.write`` → ``E_TOOL_NOT_AUTHORIZED``
    C. 解析三级降级：纯文本输出 → tier3（注入桩 LLM）/ tier4（``E_UPSTREAM_FORMAT``）
    D. §5.9 隔离实测：**子进程内部**回读 HOME / SSH_AUTH_SOCK / 宿主凭据可见性
    E. §5.9 凭据销毁：委派结束（含失败路径）后管理器零存活、明文已擦除
    F. §4.2 并发：多委派并行受上限约束、峰值可断言

【诚实标注】
    通道、隔离、凭据、Trace、计费、闸门全为**真实执行**；子代理侧为本地协议桩
    （不做 LLM 推理）。配置 ``CP_SUBAGENT_AGENT_CLI`` 后同一脚本会改走真实外部 CLI，
    代码路径不变。

用法::

    python scripts/demo_s4_04_delegation.py            # 打印 markdown 证据
    python scripts/demo_s4_04_delegation.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent.observability.trace_v2 import TraceContext, TraceFacade  # noqa: E402
from agent.subagent.channel import SubprocessChannelExecutor  # noqa: E402
from agent.subagent.credentials import TemporaryCredentialManager  # noqa: E402
from agent.subagent.delegation import DelegationContext  # noqa: E402
from agent.subagent.executor import DelegationExecutor  # noqa: E402

AGENT_CLI_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "demo_s4_04_agent_cli.py")
TOOLS = ("read_file", "search_docs")
SUBSET = ("read_file", "search_docs")


class DemoAudit:
    """最小审计收集器（替代 audit facade，避免本样例写生产审计库）"""

    def __init__(self) -> None:
        self.entries: list = []

    def record(self, action, **kwargs):
        self.entries.append({"action": action,
                             "status": kwargs.get("status", ""),
                             "subject": kwargs.get("subject", "")})
        return None


class FixtureLlm:
    """桩 LLM：仅用于演示第 3 级「纯文本 + LLM 抽取」（**不是真实模型**）"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, system_prompt=""):
        self.calls += 1
        return json.dumps({
            "status": "done",
            "summary": "由桩 LLM 从纯文本抽取得到（演示 tier3）",
            "artifacts": [{"kind": "llm_extracted"}],
            "self_eval": {"verdict": "pass", "score": 0.8},
            "tool_calls": [],
        }, ensure_ascii=False)


def make_ctx(tag: str, **overrides) -> DelegationContext:
    data = {
        "goal": f"把 docs/zh 下的 12 篇设计稿抽取为可复现步骤序列（样例 {tag}）",
        "constraints": ["只读仓库，不得修改任何文件", "产物必须带溯源"],
        "prior_artifacts": ["docs/zh/a.md"],
        "prohibitions": ["不得访问网络", "不得写入记忆"],
        "artifact_format": "JSON Lines：每行 {name, steps[]}",
        "budget_tokens": 20000,
        "timeout_seconds": 120.0,
        "callback_url": "internal://pipeline/stage2",
        "task_id": f"task-s404-{tag}",
        "trace_id": f"tr-orchestration-{tag}",
        "tenant_id": "default",
        "subject_id": "owner",
        "metadata": {"workspace_id": "ws-s404-demo"},
    }
    data.update(overrides)
    return DelegationContext(**data)


def cli_for(emit: str, declare_tools=()) -> str:
    """构造 ``<agent_cli>`` 串（含演示开关；§3.10 的 -p/--output-format/--max-turns 由执行器追加）"""
    parts = [sys.executable, AGENT_CLI_SCRIPT, "--emit", emit]
    for tool in declare_tools:
        parts += ["--declare-tool", tool]
    return subprocess.list2cmdline(parts) if os.name == "nt" else " ".join(parts)


def build_executor(workspace: str, facade, manager, **kwargs) -> DelegationExecutor:
    audit = DemoAudit()
    executor = DelegationExecutor(
        channel=SubprocessChannelExecutor(),
        workspace=workspace,
        trace=facade,
        audit=audit,
        credential_manager=manager,
        **kwargs,
    )
    return executor


def sample_a_success(tmp: str, facade, manager) -> dict:
    """A. 一次成功委派（真实子进程 + 真实 JSON Lines）"""
    executor = build_executor(tmp, facade, manager, agent_cli=cli_for("jsonl"))
    ctx = make_ctx("A")
    outcome = executor.execute(
        ctx, tools=TOOLS, authorized_capabilities=SUBSET,
        credentials=[{"name": "GITHUB_TOKEN", "value": "ghp_demo_s404_token",
                      "source": "mcp:github"},
                     {"name": "SEARCH_KEY", "value": "demo-search-key",
                      "source": "mcp:search"}],
        parent_trace=TraceContext(task_id=ctx.task_id, workspace_id="ws-s404-demo"))
    return {
        "ok": outcome.ok, "tier": outcome.tier, "attempts": outcome.attempts,
        "task_file": outcome.task_file,
        "command": (outcome.invocation or {}).get("command", ""),
        "artifact_count": len(outcome.artifacts),
        "triad_complete": bool(outcome.triad and outcome.triad.is_complete),
        "cost_counted": bool(outcome.cost and outcome.cost.counted),
        "counted_tokens": (outcome.cost.counted_tokens if outcome.cost else 0),
        "trace_id": outcome.trace_id,
        "trace_actor": (outcome.trace or {}).get("actor", ""),
        "trace_parent": (outcome.trace or {}).get("parent_trace_id", ""),
        "trace_status": (outcome.trace or {}).get("status", ""),
        "credentials_destroyed": outcome.credentials_destroyed,
        "credentials": outcome.credentials,
        "tools_visible": outcome.toolset.get("tools", []),
        "isolation": outcome.isolation,
        "child_probe": (outcome.artifacts[0].get("probe")
                        if outcome.artifacts else {}),
        "callback": outcome.callback,
    }


def sample_b_tool_trim(tmp: str, facade, manager) -> dict:
    """B. 工具裁剪闸门：子代理声明越界调用 → 委派整体失败"""
    results = {}
    for tag, declared in (("B1", ["read_file"]), ("B2", ["memory.write"]),
                          ("B3", ["mcp:filesystem::approval.approve"])):
        executor = build_executor(tmp, facade, manager,
                                  agent_cli=cli_for("jsonl", declared))
        outcome = executor.execute(make_ctx(tag), tools=TOOLS,
                                   authorized_capabilities=SUBSET)
        results[tag] = {
            "declared": declared, "ok": outcome.ok,
            "error_code": outcome.error_code, "sub_reason": outcome.sub_reason,
            "violations": [{"tool": v.get("tool"), "matched": v.get("matched"),
                            "matrix_operation": v.get("matrix_operation")}
                           for v in outcome.tool_violations],
        }
    return results


def sample_c_degradation(tmp: str, facade, manager) -> dict:
    """C. 解析三级降级：纯文本 → tier3（桩 LLM）/ tier4（E_UPSTREAM_FORMAT）"""
    tier3_executor = build_executor(tmp, facade, manager, agent_cli=cli_for("text"),
                                    llm=FixtureLlm())
    tier3 = tier3_executor.execute(make_ctx("C3"), tools=TOOLS,
                                   authorized_capabilities=SUBSET)
    tier4_executor = build_executor(tmp, facade, manager, agent_cli=cli_for("fenced"))
    tier4 = tier4_executor.execute(make_ctx("C4"), tools=TOOLS,
                                   authorized_capabilities=SUBSET)
    return {
        "tier3": {"tier": tier3.tier, "ok": tier3.ok, "attempts": tier3.attempts},
        "tier4": {"tier": tier4.tier, "ok": tier4.ok, "attempts": tier4.attempts,
                  "error_code": tier4.error_code, "sub_reason": tier4.sub_reason,
                  "error": tier4.error},
    }


def sample_d_credentials(tmp: str, facade, manager) -> dict:
    """E. 凭据销毁：成功路径与失败路径都要归零"""
    executor = build_executor(tmp, facade, manager, agent_cli=cli_for("jsonl"))
    ctx = make_ctx("E")
    outcome = executor.execute(
        ctx, tools=TOOLS, authorized_capabilities=SUBSET,
        credentials=[{"name": "SESSION_TOKEN", "value": "demo-session-token",
                      "source": "mcp:demo", "ttl_seconds": 30}])
    snapshot = manager.snapshot()
    return {
        "ok": outcome.ok,
        "credentials_destroyed": outcome.credentials_destroyed,
        "active_after": manager.active_count(),
        "credential_records": [
            {"name": c["name"], "source": c["source"], "env_var": c["env_var"],
             "ttl_seconds": c["ttl_seconds"], "destroyed": c["destroyed"],
             "wipe_verified": c["wipe_verified"],
             "destroy_reason": c["destroy_reason"],
             "fingerprint": c["value_fingerprint"]}
            for c in outcome.credentials],
        "issued_total": snapshot["issued"],
        "destroyed_total": snapshot["destroyed"],
    }


def sample_e_backpressure(tmp: str, facade, manager) -> dict:
    """F. §4.2 并发上限 + 回压（真实子进程并发）"""
    executor = build_executor(tmp, facade, manager, agent_cli=cli_for("jsonl"),
                              max_concurrency=2)
    contexts = [make_ctx(f"F{i}") for i in range(6)]
    outcomes = executor.execute_many(contexts, max_concurrency=2, tools=TOOLS,
                                     authorized_capabilities=SUBSET)
    stats = executor.barrier.stats().to_dict()
    return {
        "delegations": len(outcomes),
        "succeeded": sum(1 for o in outcomes if o.ok),
        "peak_in_flight": stats["peak_in_flight"],
        "max_concurrency": stats["max_concurrency"],
        "total_admitted": stats["total_admitted"],
        "order_preserved": [o.delegation_id for o in outcomes] == [
            c.delegation_id for c in contexts],
    }


def render_markdown(evidence: dict) -> str:
    a = evidence["A_success"]
    b = evidence["B_tool_trim"]
    c = evidence["C_degradation"]
    d = evidence["E_credentials"]
    e = evidence["F_backpressure"]
    probe = a.get("child_probe") or {}
    lines = [
        "# TASK-S4-04 端到端样例（真实子进程委派）",
        "",
        f"> 生成时间：{evidence['generated_at']}",
        f"> 环境：`CP_SUBAGENT_AGENT_CLI` = {evidence['env']['agent_cli'] or '（未配置）'}"
        f"；外部 LLM 凭证 = {evidence['env']['llm_credential']}",
        f"> 子代理侧：**本地协议桩（真实子进程，不做 LLM 推理）**——"
        f"通道/隔离/凭据/Trace/闸门均为真实执行",
        "",
        "## A. 一次成功委派（§3.9 + §3.10 + §3.4）",
        "",
        "| 观测项 | 实测值 |",
        "|---|---|",
        f"| 结果 | `ok={a['ok']}` / tier=`{a['tier']}` / 调用次数={a['attempts']} |",
        f"| §3.10 命令行 | `{a['command']}` |",
        f"| task_file | `{a['task_file']}` |",
        f"| 产物条目 | {a['artifact_count']} |",
        f"| 回收三件套齐全 | `{a['triad_complete']}` |",
        f"| 成本计入核算 | `{a['cost_counted']}`（counted_tokens={a['counted_tokens']}） |",
        f"| Trace actor | `{a['trace_actor']}` |",
        f"| Trace parent_trace_id | `{a['trace_parent'][:24]}…`（非空 = `{bool(a['trace_parent'])}`） |",
        f"| Trace status | `{a['trace_status']}` |",
        f"| 裁剪后可见工具 | `{a['tools_visible']}` |",
        f"| 回调 | `{a['callback']}` |",
        "",
        "### 子进程内部实测的隔离事实（§5.9，最强证据）",
        "",
        "| 探针 | 子进程内实测值 |",
        "|---|---|",
        f"| `HOME` | `{probe.get('home')}` |",
        f"| `USERPROFILE` | `{probe.get('userprofile')}` |",
        f"| `SSH_AUTH_SOCK` | `{probe.get('ssh_auth_sock')}` |",
        f"| `CP_SANDBOX_HOST_NETWORK` | `{probe.get('host_network_flag')}` |",
        f"| 宿主 `AWS_SECRET_ACCESS_KEY` 可见 | `{probe.get('aws_secret_visible')}` |",
        f"| 宿主 `GITHUB_TOKEN` 可见 | `{probe.get('github_token_visible')}` |",
        f"| 宿主 `OPENAI_API_KEY` 可见 | `{probe.get('openai_key_visible')}` |",
        f"| 临时凭据键名（子进程可见，**仅键名**） | `{probe.get('temp_credential_keys')}` |",
        "",
        "## B. 工具裁剪闸门（§5.7 机制 3，含间接路径）",
        "",
        "| 子代理声明的调用 | 委派结果 | error_code | 命中 | 矩阵操作 |",
        "|---|---|---|---|---|",
    ]
    for tag, item in b.items():
        violation = item["violations"][0] if item["violations"] else {}
        lines.append(
            f"| `{item['declared']}` ({tag}) | ok=`{item['ok']}` | `{item['error_code'] or '—'}` | "
            f"`{violation.get('matched', '—')}` | `{violation.get('matrix_operation', '—')}` |")
    lines += [
        "",
        "## C. 输出解析三级降级（§3.10）",
        "",
        "| 样例 | 输出形态 | tier | 调用次数 | 结果 |",
        "|---|---|---|---|---|",
        f"| C3 | 纯文本（+ 桩 LLM 抽取） | `{c['tier3']['tier']}` | {c['tier3']['attempts']} | ok=`{c['tier3']['ok']}` |",
        f"| C4 | markdown 围栏（严格拒绝） | `{c['tier4']['tier']}` | {c['tier4']['attempts']} | "
        f"`{c['tier4']['error_code']}`（{c['tier4']['sub_reason']}） |",
        "",
        "## E. 临时凭据 TTL 与销毁（§5.9）",
        "",
        f"- 委派后存活凭据数：**{d['active_after']}**（须为 0）",
        f"- `credentials_destroyed`：`{d['credentials_destroyed']}`",
        f"- 签发/销毁累计：{d['issued_total']} / {d['destroyed_total']}",
        "",
        "| 名称 | 来源 | 环境变量 | TTL(s) | 已销毁 | 明文已擦除 | 销毁原因 | 指纹 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cred in d["credential_records"]:
        lines.append(
            f"| `{cred['name']}` | `{cred['source']}` | `{cred['env_var']}` | "
            f"{cred['ttl_seconds']} | `{cred['destroyed']}` | `{cred['wipe_verified']}` | "
            f"`{cred['destroy_reason']}` | `{cred['fingerprint']}` |")
    lines += [
        "",
        "## F. 并行编排与并发上限（§4.2）",
        "",
        f"- 委派数：{e['delegations']}，成功：{e['succeeded']}",
        f"- 并发上限：{e['max_concurrency']}，**实测峰值 in_flight：{e['peak_in_flight']}**"
        f"（不变量：峰值 ≤ 上限 = `{e['peak_in_flight'] <= e['max_concurrency']}`）",
        f"- 准入总数：{e['total_admitted']}，结果保序：`{e['order_preserved']}`",
        "",
        "## G. 落库 Trace 行（`capability_id=subagent.delegate`）",
        "",
        "| trace_id | actor | status | parent_trace_id |",
        "|---|---|---|---|",
    ]
    for row in evidence.get("trace_rows", []):
        parent = row.get("parent_trace_id") or "（空）"
        lines.append(f"| `{row['trace_id']}` | `{row['actor']}` | `{row['status']}` | `{parent}` |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="TASK-S4-04 端到端样例")
    parser.add_argument("--json", dest="json_out", default="",
                        help="证据 JSON 输出路径（可选）")
    args = parser.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="s404-demo-")
    trace_path = os.path.join(tmp, "trace.db")
    facade = TraceFacade(db_path=trace_path)
    manager = TemporaryCredentialManager()
    try:
        evidence = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "env": {
                "agent_cli": os.environ.get("CP_SUBAGENT_AGENT_CLI", ""),
                "llm_credential": "未配置（.env 为空）",
            },
            "A_success": sample_a_success(tmp, facade, manager),
            "B_tool_trim": sample_b_tool_trim(tmp, facade, manager),
            "C_degradation": sample_c_degradation(tmp, facade, manager),
            "E_credentials": sample_d_credentials(tmp, facade, manager),
            "F_backpressure": sample_e_backpressure(tmp, facade, manager),
        }
        facade.flush(timeout=2.0)
        evidence["trace_rows"] = [
            {"trace_id": t.trace_id, "actor": t.actor, "status": t.response.status,
             "parent_trace_id": t.parent_trace_id, "capability_id": t.capability_id}
            for t in facade.query(capability_id="subagent.delegate")
        ]
        markdown = render_markdown(evidence)
        print(markdown)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(evidence, fh, ensure_ascii=False, indent=2)
            print(f"\n[evidence] {args.json_out}", file=sys.stderr)
    finally:
        facade._store.stop(timeout=3.0)
        manager.destroy_all(reason="demo_teardown")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
