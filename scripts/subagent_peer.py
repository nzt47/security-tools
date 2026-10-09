#!/usr/bin/env python3
"""分身侧 CLI 对端 —— container / 换机执行的参考入口（§3.10 task_file-jsonl）

【为什么需要这个文件（不这样会怎样）】
    ``agent/subagent/container_backend.py`` 已经把 §3.10 的 task_file-jsonl 协议
    包进 ``docker run``，但镜像是空的：没有可执行对端时，容器只会报
    "executable file not found"，"container 档"就只是 argv 契约、不是"在哪跑"。
    本文件就是镜像里那个可执行对端（参考镜像见 ``docker/subagent-peer/Dockerfile``）。

【它做什么、不做什么（反幻觉）】
    做：严格实现 §3.10 命令行 ``-p <task_file> --output-format json --max-turns N``，
    读 task_file，向 stdout 逐行输出 JSON 对象；``--network none`` + 只读根也能
    完成一次真实协议往返。
    不做：默认**不**伪造 LLM 回答。默认态输出一份 ``offline_receipt`` —— 如实报告
    读到了什么、八要素缺不缺、预算多少。``--network none`` 的容器里没有推理后端，
    编一段 summary 只会污染回收三件套与成本账。
    要真跑推理：``--handler module:callable`` 注入镜像内的执行体（例如自带 Ollama
    的 local 包装）。handler 是 ``--handler`` 的唯一消费者，抛错即非零退出且 stdout
    留空（母体按 E_UPSTREAM_FORMAT 如实记失败），绝不返回"假成功"。

【为什么是单文件、零仓库导入】
    镜像要能是 ``python:3.12-slim`` + 这一个文件。``import agent.subagent.channel``
    会先执行 ``agent/subagent/__init__.py`` 的一整串重导入（executor / lifecycle /
    memory），在只装标准库的瘦镜像里直接 ImportError。因此本文件不导入仓库模块，
    协议常量在此重述；``tests/unit/test_subagent_peer_entrypoint.py`` 把重述值与
    ``channel`` / ``delegation`` 的唯一权威逐字对拍 —— 重述 + 对拍守卫，而不是
    "两处各写一遍没人管"。

【退出码】
    0 成功；2 参数/输出格式错误；3 task_file 不可读或不是对象；
    4 handler 规格/导入/执行失败。失败时 stdout 保持为空，诊断只进 stderr。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys

#: §3.10 命令行协议尾巴（唯一权威：agent/subagent/channel.py::CLI_ARGV_TAIL）
PROTOCOL_TAIL = ("-p", "--output-format", "--max-turns")
#: 协议默认值（唯一权威：channel.DEFAULT_OUTPUT_FORMAT / DEFAULT_MAX_TURNS）
DEFAULT_OUTPUT_FORMAT = "json"
DEFAULT_MAX_TURNS = 10
#: entrypoint 协议名（唯一权威：bundle.BUNDLE_PROTOCOL）
PROTOCOL = "task_file-jsonl"
#: 八要素（唯一权威：delegation.EIGHT_ELEMENTS）
EIGHT_ELEMENTS = (
    "goal", "constraints", "prior_artifacts", "prohibitions",
    "artifact_format", "budget_tokens", "timeout_seconds", "callback_url",
)
#: 离线回执的中性状态名（不冒充 done）
RECEIPT_STATUS = "offline_receipt"


def build_parser() -> argparse.ArgumentParser:
    """构造与 §3.10 逐字一致的命令行解析器（-p / --output-format / --max-turns）"""
    parser = argparse.ArgumentParser(
        prog="subagent_peer",
        description="分身侧 CLI 对端（§3.10 task_file-jsonl 协议）")
    parser.add_argument("-p", "--task-file", dest="task_file", required=True,
                        help="八要素上下文包（task_file.json）")
    parser.add_argument("--output-format", dest="output_format",
                        default=DEFAULT_OUTPUT_FORMAT)
    parser.add_argument("--max-turns", dest="max_turns", type=int,
                        default=DEFAULT_MAX_TURNS)
    parser.add_argument("--handler", dest="handler", default="",
                        help="镜内执行体 module:callable（缺省 = 离线回执）")
    return parser


def build_receipt(task_file: dict, *, max_turns: int, output_format: str) -> dict:
    """离线协议回执：只报"读到了什么"，不写一句编造的结论"""
    present = [name for name in EIGHT_ELEMENTS
               if task_file.get(name) not in (None, "", [], {})]
    missing = [name for name in EIGHT_ELEMENTS if name not in present]
    return {
        "status": RECEIPT_STATUS,
        "protocol": PROTOCOL,
        "task_id": str(task_file.get("task_id") or task_file.get("delegation_id") or ""),
        "delegation_id": str(task_file.get("delegation_id") or ""),
        "elements_present": present,
        "elements_missing": missing,
        "max_turns": int(max_turns),
        "output_format": str(output_format),
        "inference": "none",
        "network_required": False,
        "note": ("离线对端回执：未调用任何推理后端，也未伪造 LLM 回答；"
                 "要真跑推理请用 --handler 注入镜内执行体。"),
        "_turns_used": 0,
        "_max_turns": int(max_turns),
    }


def load_handler(spec: str):
    """把 ``module:callable`` 解析成可调用对象（规格非法即抛 ValueError）"""
    module_name, _, attr = str(spec or "").partition(":")
    if not module_name.strip() or not attr.strip():
        raise ValueError("--handler 必须是 module:callable，收到 %r" % spec)
    module = importlib.import_module(module_name.strip())
    func = getattr(module, attr.strip(), None)
    if not callable(func):
        raise ValueError("--handler 目标不可调用：%r" % spec)
    return func


def normalize_records(result) -> list:
    """把 handler 返回值规整为 JSON 对象列表（非 dict 即抛，不静默丢弃）"""
    if isinstance(result, dict):
        return [result]
    if isinstance(result, (list, tuple)):
        if any(not isinstance(item, dict) for item in result):
            raise ValueError("handler 返回的列表只能含 JSON 对象")
        if not result:
            raise ValueError("handler 返回了空列表（无输出不是成功）")
        return list(result)
    raise ValueError("handler 必须返回 dict 或 list[dict]")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.output_format != DEFAULT_OUTPUT_FORMAT:
        print("只支持 --output-format %s，收到 %r"
              % (DEFAULT_OUTPUT_FORMAT, args.output_format), file=sys.stderr)
        return 2
    if int(args.max_turns) <= 0:
        print("--max-turns 必须为正整数，收到 %r" % args.max_turns, file=sys.stderr)
        return 2

    try:
        with open(args.task_file, "r", encoding="utf-8") as fh:
            task_file = json.load(fh)
    except (OSError, ValueError) as exc:
        print("task_file 不可读: %s" % exc, file=sys.stderr)
        return 3
    if not isinstance(task_file, dict):
        print("task_file 必须是 JSON 对象，实际为 %s"
              % type(task_file).__name__, file=sys.stderr)
        return 3

    if args.handler:
        try:
            handler = load_handler(args.handler)
            result = handler(task_file, max_turns=int(args.max_turns),
                             output_format=str(args.output_format))
            records = normalize_records(result)
        except Exception as exc:  # noqa: BLE001 对端失败必须非零，绝不产出"假成功"
            print("handler 执行失败: %s" % exc, file=sys.stderr)
            return 4
    else:
        records = [build_receipt(task_file, max_turns=args.max_turns,
                                 output_format=args.output_format)]

    for record in records:
        # 每行一个 JSON 对象：§3.10 的 JSON Lines 判定（缩进/围栏一律不合格）
        print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
