"""规划质量评测脚本（阶段 4：规划评测基线 KPI）

运行全部/指定类别的评测任务（tests/eval/planning_eval_set.py），输出 KPI 报告：
- 成功率、平均迭代、平均成本、平均耗时（来自 PlanningMetrics 双通道聚合）；
- 失败原因分布（executor D14 四分类写入 task.metadata["failure_reason"]）；
- 分类型统计（simple / multi_step / parallel / failure_injection）。

用法:
    python scripts/eval_planning.py [--category simple|multi_step|parallel|failure_injection]
                                    [--quiet]
                                    [--output <json_path>]

说明：
- simple 任务走 core.chat 直连（期望 used_planning=False）；
- multi/parallel/failure 走 plan + execute_plan；
- parallel 任务用 mock LLM（llm_responses）分解无依赖任务，
  需 config.executor.parallel_execution=true 启用并发；
- fail_tool_timeout 用例单独配置 executor.tool_timeout=0.5 触发超时；
- 每个用例独立 PlanningCore（独立临时目录，planning.storage 关闭，
  不落盘 data/plans，保证评测可复现）。
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime

# 项目根入 sys.path（脚本位于 scripts/ 下）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "eval"))

from planning.core import PlanningCore
from planning.models import PlanState
from planning_eval_set import get_eval_set


class _SeqLLM:
    """按序消费响应的 mock LLM（chat 返回列表中的下一条）"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return "{}"


def _build_config(task: dict, tmp_dir: str) -> dict:
    """按用例构建 PlanningCore 配置（storage 关闭 + 独立临时目录）"""
    config = {
        "reflector": {"persist_dir": tmp_dir},
        "planning": {"persist_dir": tmp_dir, "storage": {"enabled": False}},
    }
    executor_cfg = {}
    if task["category"] == "parallel":
        executor_cfg["parallel_execution"] = True
    if task["id"] == "fail_tool_timeout":
        executor_cfg["tool_timeout"] = 0.5
    if executor_cfg:
        config["executor"] = executor_cfg
    return config


async def _run_plan_path(core: PlanningCore, desc: str) -> dict:
    """plan + execute_plan 路径，返回 (success, iterations, fail_reason)"""
    plan = await core.plan(desc)
    if plan.state == PlanState.FAILED:
        return {"success": False, "iterations": 0, "fail_reason": plan.error or "分解失败"}
    core._active_plans.pop(plan.id, None)
    executed = await core.execute_plan(plan)
    failed_task = next(
        (t for t in executed.tasks if t.status.value == "failed"), None
    )
    if failed_task is not None:
        fail_reason = (
            (failed_task.metadata or {}).get("failure_reason")
            or (failed_task.error or "执行失败")
        )
    else:
        fail_reason = None
    return {
        "success": executed.is_success(),
        "iterations": executed.current_step,
        "fail_reason": fail_reason,
    }


async def run_single_case(task: dict) -> dict:
    """执行单个评测任务，返回结果 dict"""
    task_id = task["id"]
    category = task["category"]
    desc = task["description"]

    with tempfile.TemporaryDirectory() as tmp_dir:
        llm = None
        if category == "simple":
            llm = _SeqLLM([f"已收到: {desc}"])
        elif task.get("llm_responses"):
            llm = _SeqLLM(task["llm_responses"])

        core = PlanningCore(llm_service=llm, config=_build_config(task, tmp_dir))
        for name, fn in (task.get("tools") or {}).items():
            core.register_tool(name, fn)

        started = datetime.now()
        try:
            if not task.get("expect_planning", True):
                result = await core.chat(desc, {"session": "eval"})
                path = "chat"
                success = (result.used_planning is False) and bool(result.response)
                iterations = 0
                fail_reason = None
            else:
                path = "plan+execute"
                outcome = await _run_plan_path(core, desc)
                success = outcome["success"]
                iterations = outcome["iterations"]
                fail_reason = outcome["fail_reason"]

            expected = bool(task.get("expect_success", True))
            duration_ms = (datetime.now() - started).total_seconds() * 1000
            return {
                "id": task_id,
                "category": category,
                "path": path,
                "success": success,
                "expected": expected,
                "passed": success == expected,
                "iterations": iterations,
                "duration_ms": round(duration_ms, 1),
                "fail_reason": fail_reason,
                "metrics": core.get_planning_metrics(),
            }
        finally:
            core._active_plans.clear()


async def run_eval(category: str = None, quiet: bool = False) -> list:
    """运行评测集，返回各用例结果列表"""
    tasks = get_eval_set(category)
    results = []
    for task in tasks:
        result = await run_single_case(task)
        results.append(result)
        if not quiet:
            status = "PASS" if result["passed"] else "FAIL"
            print(
                f"[{status}] {result['id']:<22} {result['category']:<18}"
                f" path={result['path']} success={result['success']}"
                f" iter={result['iterations']}"
                f" ({result['duration_ms']:.0f}ms)"
                + (f" fail={result['fail_reason']}" if result["fail_reason"] else "")
            )
    return results


def _aggregate(results: list) -> dict:
    """汇总 KPI：成功率 / 平均迭代 / 平均成本 / 平均耗时 / 失败原因分布 / 分类型"""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    metrics = [r["metrics"] for r in results]
    plans_total = sum(m["plans"]["total"] for m in metrics)
    iterations_total = sum(m["iterations_total"] for m in metrics)
    cost_total = sum(m["cost_total"] for m in metrics)
    # duration_ms 为 {count, avg} 聚合结构，按 count 加权求总平均
    duration_count = sum(m["duration_ms"]["count"] for m in metrics)
    duration_weighted = sum(
        m["duration_ms"]["avg"] * m["duration_ms"]["count"] for m in metrics
    )
    duration_avg = (duration_weighted / duration_count) if duration_count else 0.0
    # 经验命中率：跨全部用例聚合（by_task_type 含 queries/hits 计数）
    total_queries = sum(
        v["queries"]
        for m in metrics if m["enabled"]
        for v in (m["experience_hit_rate"] or {}).get("by_task_type", {}).values()
    )
    total_hits = sum(
        v["hits"]
        for m in metrics if m["enabled"]
        for v in (m["experience_hit_rate"] or {}).get("by_task_type", {}).values()
    )
    exp_overall = round(total_hits / total_queries, 4) if total_queries else 0.0

    fail_counter = Counter(r["fail_reason"] for r in results if not r["success"])
    by_category = {}
    for cat in sorted({r["category"] for r in results}):
        cat_results = [r for r in results if r["category"] == cat]
        cat_total = len(cat_results)
        cat_passed = sum(1 for r in cat_results if r["passed"])
        by_category[cat] = {
            "cases": cat_total,
            "passed": cat_passed,
            "success_rate": round(cat_passed / cat_total, 4) if cat_total else 0.0,
        }

    return {
        "timestamp": datetime.now().isoformat(),
        "cases_total": total,
        "passed": passed,
        "success_rate": round(passed / total, 4) if total else 0.0,
        "plans_total": plans_total,
        "iterations_avg": round(iterations_total / plans_total, 2) if plans_total else 0.0,
        "cost_total": round(cost_total, 4),
        "duration_ms_avg": round(duration_avg, 1),
        "experience_hit_rate": exp_overall,
        "failure_reason_distribution": dict(fail_counter),
        "by_category": by_category,
    }


def _format_report(aggregate: dict) -> str:
    """生成 KPI 报告（markdown 文本，供 stdout 与基线存档复用）"""
    lines = [
        "## 规划质量评测 KPI 报告",
        "",
        f"- 评测时间: {aggregate['timestamp']}",
        f"- 用例总数: {aggregate['cases_total']}（通过 {aggregate['passed']}）",
        f"- 成功率: {aggregate['success_rate']:.2%}",
        f"- 计划总数: {aggregate['plans_total']}",
        f"- 平均迭代: {aggregate['iterations_avg']}",
        f"- 累计成本: {aggregate['cost_total']}",
        f"- 平均耗时: {aggregate['duration_ms_avg']}ms",
        f"- 经验命中率: {aggregate['experience_hit_rate']}",
        "",
        "### 失败原因分布",
        "",
    ]
    dist = aggregate["failure_reason_distribution"] or {"（无失败用例）": 0}
    lines.extend([f"- {k}: {v}" for k, v in dist.items()])
    lines += ["", "### 分类型统计", ""]
    for cat, stats in aggregate["by_category"].items():
        lines.append(
            f"- {cat}: {stats['passed']}/{stats['cases']}"
            f"（成功率 {stats['success_rate']:.2%}）"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="规划质量评测（阶段 4 基线）")
    parser.add_argument("--category", default=None,
                        help="仅运行指定分类（simple/multi_step/parallel/failure_injection）")
    parser.add_argument("--quiet", action="store_true", help="不逐用例打印")
    parser.add_argument("--output", default=None, help="KPI 报告/结果写入 JSON 文件")
    args = parser.parse_args()

    results = asyncio.run(run_eval(args.category, args.quiet))
    aggregate = _aggregate(results)

    print("\n" + "=" * 60)
    print(_format_report(aggregate))
    print("=" * 60)

    if args.output:
        payload = {"aggregate": aggregate, "results": results}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入: {args.output}")


if __name__ == "__main__":
    main()
