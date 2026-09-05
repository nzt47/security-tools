# -*- coding: utf-8 -*-
"""演示: 云枢成功工具交互 → 自动沉淀免 LLM workflow（生产仓库）

模拟云枢执行典型"工具链任务"的成功交互（工具名取自云枢真实注册表：
read_file / search_files / write_file / shell_execute / compress /
decompress / json_query 等），经 workflow_learning 自动学习链路沉淀进
data/learned_workflows.json。之后云枢对话中遇到相似请求可 0-Token 命中
（免 LLM）直接执行工具链。

用法：python scripts/seed_demo_workflows.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.workflow_learning import WorkflowLearningService
from agent.workflow_learning.models import LearningRecord

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
            {"name": "json_to_yaml", "params": {}, "success": True},
            {"name": "write_file", "params": {"path": "config.yaml"},
             "success": True},
        ],
    },
]


def main():
    svc = WorkflowLearningService()  # 生产仓库 data/learned_workflows.json
    svc.set_tool_executor(
        lambda tool_name, params: {"ok": True, "tool": tool_name,
                                   "result": "模拟执行成功"})
    before = len(svc.repo.list_all())
    print(f"沉淀前 workflow 数: {before}")

    for rec in DEMO_RECORDS:
        wf = svc.learn_from_interaction(LearningRecord(
            session_id="demo-seed",
            user_input=rec["user_input"],
            tool_calls=rec["tool_calls"],
            success=True,
        ))
        print(f"[沉淀] {wf.id}: {wf.name} "
              f"steps={[s.tool_name for s in wf.steps]} "
              f"conf={wf.confidence}")

    after = len(svc.repo.list_all())
    print(f"\n沉淀后 workflow 数: {after} (新增 {after - before})")
    print("\n验证: 相似请求应命中并免 LLM 执行")
    for q in ["统计这个项目 Python 文件行数保存成报告",
              "把代码打包 zip"]:
        res = svc.try_execute(q, min_score=0.25)
        print(f"  [{q}] matched={res.matched} "
              f"steps={res.steps_executed} success={res.success}")


if __name__ == "__main__":
    main()
