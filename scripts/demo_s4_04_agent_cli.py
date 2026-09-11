"""S4-04 端到端样例用的本地 agent CLI 桩（**真实进程**，遵守 §3.10 物理协议）

【定位与诚实标注】
    本脚本是**外部子代理 agent CLI 的替身**，用于在没有第三方 agent CLI / LLM 凭证的
    环境里（本仓库 `.env` 为空、``CP_SUBAGENT_AGENT_CLI`` 未配置）产出**可复现**的
    端到端证据。它**不做任何 LLM 推理**——只按 task_file 的八要素生成一份确定性产物。

    但通道侧的一切都是**真的**：
      - 真实子进程（``subprocess`` spawn，非进程内桩）；
      - 真实 ``task_file.json`` 落盘 → 由子进程读回（§3.10 ``<cli> -p <task_file.json>``）；
      - 真实 stdout JSON Lines（由云枢侧按三级降级解析）；
      - 真实环境隔离（子进程**从内部**回读 $HOME / SSH_AUTH_SOCK / 宿主凭据）；
      - 真实临时凭据注入（子进程只看到 ``CP_TEMP_*``，看不到宿主长期密钥）。

    因此本脚本提供的是「**协议与安全的真实证据**」；「模型推理质量」不属本样例范围
    （那需要真实 LLM，见 ``CP_SUBAGENT_AGENT_CLI`` 配置后的同一路径）。

用法（与 §3.10 命令行一致）::

    python scripts/demo_s4_04_agent_cli.py -p <task_file.json> --output-format json --max-turns 5

附加开关（仅用于演示降级/越界路径）::

    --emit jsonl|text|fenced   输出形态（text → 触发第 3 级抽取；fenced → 触发严格拒绝）
    --declare-tool NAME        在产物里声明调用过某工具（用于工具裁剪闸门证据）

退出码：0 成功；2 参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _isolation_probe() -> dict:
    """**从子进程内部**回读隔离事实（最强证据：不是宿主侧的假定，而是子进程的实测）

    Returns:
        dict: HOME / SSH_AUTH_SOCK / 宿主网络开关 / 宿主长期凭据是否可见 /
            临时凭据键名（**只回键名，不回明文**）。
    """
    temp_keys = sorted(k for k in os.environ if k.startswith("CP_TEMP_"))
    return {
        "home": os.environ.get("HOME", "<unset>"),
        "userprofile": os.environ.get("USERPROFILE", "<unset>"),
        "ssh_auth_sock": os.environ.get("SSH_AUTH_SOCK", "<unset>"),
        "host_network_flag": os.environ.get("CP_SANDBOX_HOST_NETWORK", "<unset>"),
        "aws_secret_visible": "AWS_SECRET_ACCESS_KEY" in os.environ,
        "github_token_visible": "GITHUB_TOKEN" in os.environ,
        "openai_key_visible": "OPENAI_API_KEY" in os.environ,
        "temp_credential_keys": temp_keys,
        "cwd": os.getcwd(),
    }


def _build_artifact(task_file: dict, declare_tools: list) -> dict:
    """按八要素契约生成确定性产物（不是推理结果，是协议夹具）"""
    goal = str(task_file.get("goal") or "")
    constraints = list(task_file.get("constraints") or [])
    prohibitions = list(task_file.get("prohibitions") or [])
    artifact_format = str(task_file.get("artifact_format") or "")
    returns = {
        "status": "done",
        "summary": f"已按契约处理目标：{goal[:80]}",
        "artifacts": [{
            "kind": "protocol_fixture",
            "format": artifact_format,
            "constraints_honoured": len(constraints),
            "prohibitions_honoured": len(prohibitions),
            "probe": _isolation_probe(),
        }],
        "self_eval": {
            "verdict": "pass",
            "score": 0.9,
            "summary": "本地 CLI 桩按协议产出确定性产物（非 LLM 推理）",
            "issues": [],
        },
        "tool_calls": [{"name": name} for name in declare_tools],
        "tokens": {"input_tokens": 1000, "output_tokens": 500},
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_usd": 0.0,
        "channel_meta": {
            "protocol": "<agent_cli> -p <task_file.json> --output-format json --max-turns N",
            "agent_cli_role": "local_protocol_fixture",
            "llm_used": False,
        },
    }
    return returns


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="S4-04 端到端样例用本地 agent CLI 桩")
    # §3.10：<agent_cli> -p <task_file.json> --output-format json --max-turns N
    parser.add_argument("-p", dest="task_file", required=True,
                        help="八要素上下文包（task_file.json）")
    parser.add_argument("--output-format", dest="output_format", default="json")
    parser.add_argument("--max-turns", dest="max_turns", type=int, default=10)
    parser.add_argument("--emit", dest="emit", default="jsonl",
                        choices=["jsonl", "text", "fenced"],
                        help="输出形态（text/fenced 用于演示解析降级）")
    parser.add_argument("--declare-tool", dest="declare_tools", action="append",
                        default=[], help="在产物里声明调用过某工具（裁剪闸门证据）")
    args = parser.parse_args(argv)

    try:
        with open(args.task_file, "r", encoding="utf-8") as fh:
            task_file = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"task_file 不可读: {exc}", file=sys.stderr)
        return 2
    if not isinstance(task_file, dict):
        print("task_file 必须是 JSON 对象", file=sys.stderr)
        return 2

    artifact = _build_artifact(task_file, args.declare_tools)
    payload = json.dumps(artifact, ensure_ascii=False)

    if args.emit == "text":
        # 纯文本：触发「第 3 级：纯文本 + LLM 抽取」
        print(f"任务已完成。摘要：{artifact['summary']}")
    elif args.emit == "fenced":
        # markdown 围栏：严格 JSON Lines 判定必须拒绝（再重试一次仍拒绝）
        print("```json")
        print(payload)
        print("```")
    else:
        # 合规 JSON Lines：单行一个 JSON 对象
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
