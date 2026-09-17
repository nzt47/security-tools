# -*- coding: utf-8 -*-
"""演示: 云枢成功工具交互 → 自动沉淀免 LLM workflow（**不默认写生产仓库**）

模拟云枢执行典型"工具链任务"的成功交互（工具名取自云枢真实注册表：
read_file / search_files / write_file / shell_execute / compress /
decompress / json_query 等），经 workflow_learning 自动学习链路沉淀进
**由 `--repo` 显式指定的**工作流仓库。之后云枢对话中遇到相似请求可 0-Token
命中（免 LLM）直接执行工具链。

安全约束（TASK-S11-02 加固）
---------------------------
加固前：脚本**直连生产仓库** —— `WorkflowLearningService()` 无参数构造，默认
指向 `data/learned_workflows.json`，并以 `session_id="demo-seed"` 造 3 条演示
数据。现存 3 条 `demo-seed` 条目（`json-30a189b6` / `python-eb17ed25` /
`zip-d2968c59`）即出自它。风险有两层：

  1. 违反"**不得用脚本造任务/造数据刷指标**"的纪律（`session_id="demo-seed"`
     是机器可识别的伪造来源标记，却与真实学习条目同池参与匹配）；
  2. 实测已污染真实候选池：`json-30a189b6` 曾长期作为存量里**唯一**的匹配
     候选（TASK-S10-01 报告与 S11-02 探针均可复现），即"演示数据"直接决定了
     生产环境的免 LLM 命中结果。

现加固为（任一条即可阻断默认写生产）：
  1. `--repo` **必填**：不再有默认值，省略即在**写盘前**报错退出（exit 2）；
  2. 默认只 **dry-run**：真正写盘必须**显式**加 `--apply`；
  3. dry-run 全程写入**临时目录**里的仓库文件（走完全一致的真实学习代码路径），
     生产仓库文件连"打开写"都不会发生；
  4. `--dry-run` 可显式写出（与默认同义），便于脚本化场景表达显式意图。

用法：
    # 1) 试运行（默认）：只打印"会沉淀什么"，不写任何真实数据
    python scripts/seed_demo_workflows.py --repo data/learned_workflows.json
    # 2) 真正写入（必须显式 --repo + --apply）
    python scripts/seed_demo_workflows.py --repo data/learned_workflows.json --apply
    # 3) 写到独立演示仓库（推荐：演示数据与生产数据物理隔离）
    python scripts/seed_demo_workflows.py --repo data/demo_workflows.json --apply
    # 4) 省略 --repo → 报错退出，什么都不写
    python scripts/seed_demo_workflows.py
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.workflow_learning import WorkflowLearningService  # noqa: E402
from agent.workflow_learning.models import LearningRecord  # noqa: E402

# 演示用工具链任务（每条 = 一次成功交互的 tool_calls 记录）
DEMO_RECORDS = [
    {
        "user_input": "统计项目里所有 Python 文件的行数并保存报告",
        "tool_calls": [
            {"name": "search_files", "params": {"query": "*.py",
                                                "path": "C:/project"},
             "success": True},
            {"name": "shell_execute", "params": {"cmd": "wc -l *.py"},
             "success": True},
            {"name": "write_file", "params": {"path": "report.txt"},
             "success": True},
        ],
    },
    {
        "user_input": "把代码仓库打包成 zip 压缩包",
        "tool_calls": [
            {"name": "list_directory", "params": {"path": "C:/project"},
             "success": True},
            {"name": "compress", "params": {"path": "C:/project",
                                            "format": "zip"},
             "success": True},
        ],
    },
    {
        "user_input": "读取 JSON 配置文件并转换为 YAML 格式",
        "tool_calls": [
            {"name": "read_file", "params": {"path": "config.json"},
             "success": True},
            {"name": "json_query", "params": {"query": "$"},
             "success": True},
            {"name": "data_convert", "params": {}, "success": True},
            {"name": "write_file", "params": {"path": "config.yaml"},
             "success": True},
        ],
    },
]

#: 演示数据的来源标记（机器可识别的"非真实学习"标记，便于事后隔离/清理）
DEMO_SESSION_ID = "demo-seed"


def _new_service(repo_path: str) -> WorkflowLearningService:
    svc = WorkflowLearningService(repo_path=repo_path)
    svc.set_tool_executor(
        lambda tool_name, params: {"ok": True, "tool": tool_name,
                                   "result": "模拟执行成功"})
    return svc


def _seed_into(repo_path: str, *, verbose: bool = True) -> None:
    """把演示数据沉淀进 `repo_path` 指定的仓库（**唯一**写盘入口）"""
    svc = _new_service(repo_path)
    before = len(svc.repo.list_all())
    if verbose:
        print(f"仓库: {repo_path}")
        print(f"沉淀前 workflow 数: {before}")

    for rec in DEMO_RECORDS:
        wf = svc.learn_from_interaction(LearningRecord(
            session_id=DEMO_SESSION_ID,
            user_input=rec["user_input"],
            tool_calls=rec["tool_calls"],
            success=True,
        ))
        if verbose:
            print(f"[沉淀] {wf.id}: {wf.name} "
                  f"steps={[s.tool_name for s in wf.steps]} "
                  f"conf={wf.confidence} status={wf.status}")

    after = len(svc.repo.list_all())
    if verbose:
        print(f"\n沉淀后 workflow 数: {after} (新增 {after - before})")
        print("\n验证: 相似请求应命中并免 LLM 执行")
        for q in ["统计这个项目 Python 文件行数保存成报告",
                  "把代码打包 zip"]:
            res = svc.try_execute(q, min_score=0.25)
            print(f"  [{q}] matched={res.matched} "
                  f"steps={res.steps_executed} success={res.success}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="演示工作流沉淀（默认 dry-run；写盘需 --repo + --apply）")
    ap.add_argument("--repo", required=True, metavar="PATH",
                    help="目标工作流仓库 JSON 路径（**必填**，无默认值："
                         "避免「不写路径就写生产仓库」的隐式行为）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="真正写入 --repo 指定的仓库")
    mode.add_argument("--dry-run", action="store_true",
                      help="只试运行（**默认行为**；写入临时仓库，不碰 --repo）")
    args = ap.parse_args(argv)

    target = pathlib.Path(args.repo).resolve()

    # 默认 = dry-run；只有显式 --apply 才写 --repo
    if not args.apply:
        with tempfile.TemporaryDirectory(prefix="seed_demo_wf_") as td:
            tmp_repo = pathlib.Path(td) / pathlib.Path(args.repo).name
            print("模式: DRY-RUN（不写入目标仓库）")
            print(f"目标仓库(本次未被写入): {target}")
            print(f"临时仓库: {tmp_repo}")
            print("-" * 70)
            _seed_into(str(tmp_repo))
            print("-" * 70)
            print(f"[DRY-RUN] 未写入 {target}；确认无误后加 --apply 才会真正沉淀")
        return 0

    print("模式: APPLY（写入目标仓库）")
    print("-" * 70)
    _seed_into(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
