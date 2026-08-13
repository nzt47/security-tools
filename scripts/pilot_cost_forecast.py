"""规划成本预演脚本（阶段 5 token 计费接入前的数据预演）

【不易】不改动 planning/、scripts/eval_planning.py 与评测集任何代码；
        仅通过"计费感知 mock LLM"观测若接入计费后的成本量级。
【变易】计价参数可配置（--chars-per-token 字符/token 估算、
        --price-per-1k 每千 token 单价 USD），便于按真实 API 计价回填；
        分类聚合维度与评测集一致（simple/multi_step/parallel/failure_injection）。
【简易】复用 eval_planning 的配置/路径函数，只替换 LLM 为计费感知版本，
        输出"现状 cost_total=0.0 vs 预演估算成本"对比表。

用法:
    python scripts/pilot_cost_forecast.py [--category simple|multi_step|parallel|failure_injection]
                                          [--chars-per-token 3.0]
                                          [--price-per-1k 0.002]
                                          [--output <json_path>]

预演口径:
- 现状恒 0.0 根因: Plan 路径 executor 只 record_step、从不 record_text/
  record_tokens，budget 快照 cost=0.0；评测集恰好全部走 Plan 路径。
- 预演 = 评测真实运行中全部 LLM 调用的 (prompt+response) 文本，按
  "字符数/chars_per_token" 估算 token，× 单价折算 USD。
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "eval"))

import eval_planning  # noqa: E402  复用 _build_config / _run_plan_path / _SeqLLM
from planning.core import PlanningCore  # noqa: E402
from planning_eval_set import get_eval_set  # noqa: E402


class _BillingLLM(eval_planning._SeqLLM):
    """计费感知 mock LLM：每次 chat 记录输入/输出文本长度（复用原响应队列）"""

    def __init__(self, responses, billing):
        super().__init__(responses)
        self._billing = billing

    async def chat(self, messages):
        in_text = _to_text(messages)
        response = await super().chat(messages)
        self._billing.append({
            "in_chars": len(in_text),
            "out_chars": len(response or ""),
        })
        return response


def _to_text(messages) -> str:
    """将 chat 入参归一化为纯文本（str 或 [{role, content}] 列表）"""
    if isinstance(messages, str):
        return messages
    parts = []
    for m in messages or []:
        if isinstance(m, dict):
            parts.append(str(m.get("content", "")))
        else:
            parts.append(str(m))
    return " ".join(parts)


async def _run_single_billing(task: dict, chars_per_token: float,
                              price_per_1k: float) -> dict:
    """单用例预演：复用 eval_planning 的执行路径，仅替换 LLM 为计费版本"""
    task_id = task["id"]
    category = task["category"]
    desc = task["description"]

    with tempfile.TemporaryDirectory() as tmp_dir:
        billing = []
        llm = None
        if category == "simple":
            llm = _BillingLLM([f"已收到: {desc}"], billing)
        elif task.get("llm_responses"):
            llm = _BillingLLM(task["llm_responses"], billing)

        core = PlanningCore(llm_service=llm,
                            config=eval_planning._build_config(task, tmp_dir))
        for name, fn in (task.get("tools") or {}).items():
            core.register_tool(name, fn)

        try:
            if not task.get("expect_planning", True):
                await core.chat(desc, {"session": "eval"})
                path = "chat"
            else:
                await eval_planning._run_plan_path(core, desc)
                path = "plan+execute"
        finally:
            core._active_plans.clear()

    in_tokens = sum(r["in_chars"] for r in billing) / chars_per_token
    out_tokens = sum(r["out_chars"] for r in billing) / chars_per_token
    cost = (in_tokens + out_tokens) / 1000.0 * price_per_1k
    return {
        "id": task_id,
        "category": category,
        "path": path,
        "llm_calls": len(billing),
        "in_tokens": round(in_tokens, 1),
        "out_tokens": round(out_tokens, 1),
        "cost_usd": round(cost, 8),
    }


def _aggregate(results: list) -> dict:
    total_calls = sum(r["llm_calls"] for r in results)
    total_in = sum(r["in_tokens"] for r in results)
    total_out = sum(r["out_tokens"] for r in results)
    total_cost = sum(r["cost_usd"] for r in results)
    by_category = {}
    for cat in sorted({r["category"] for r in results}):
        cat_r = [r for r in results if r["category"] == cat]
        by_category[cat] = {
            "cases": len(cat_r),
            "llm_calls": sum(r["llm_calls"] for r in cat_r),
            "in_tokens": round(sum(r["in_tokens"] for r in cat_r), 1),
            "out_tokens": round(sum(r["out_tokens"] for r in cat_r), 1),
            "cost_usd": round(sum(r["cost_usd"] for r in cat_r), 8),
        }
    return {
        "cases_total": len(results),
        "llm_calls": total_calls,
        "in_tokens": round(total_in, 1),
        "out_tokens": round(total_out, 1),
        "cost_usd": round(total_cost, 8),
        "by_category": by_category,
    }


def main():
    parser = argparse.ArgumentParser(description="规划成本预演（阶段 5 计费接入前预测）")
    parser.add_argument("--category", default=None,
                        help="仅预演指定分类（simple/multi_step/parallel/failure_injection）")
    parser.add_argument("--chars-per-token", type=float, default=3.0,
                        help="字符/token 估算（budget.py 未注入计数器时的默认近似）")
    parser.add_argument("--price-per-1k", type=float, default=0.002,
                        help="每千 token 单价 USD（budget.py 默认 0.002）")
    parser.add_argument("--output", default=None, help="预演结果写入 JSON 文件")
    args = parser.parse_args()

    tasks = get_eval_set(args.category)
    results = [
        asyncio.run(_run_single_billing(t, args.chars_per_token, args.price_per_1k))
        for t in tasks
    ]
    agg = _aggregate(results)

    print("## 规划成本预演（阶段 5 计费接入预测）")
    print(f"- 预演时间: {datetime.now().isoformat()}")
    print(f"- 估算方式: token = 字符数 / {args.chars_per_token}；"
          f"cost = tokens / 1000 × ${args.price_per_1k}")
    print(f"- 现状 cost_total: 0.0（Plan 路径 executor 未接入 token 记账）")
    print(f"- 预演成本总量: ${agg['cost_usd']:.8f} "
          f"(LLM 调用 {agg['llm_calls']} 次, 输入 {agg['in_tokens']} token, "
          f"输出 {agg['out_tokens']} token)")
    print()
    print("| 用例 | 分类 | 路径 | LLM调用 | 输入token | 输出token | 成本($) |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['id']} | {r['category']} | {r['path']} | {r['llm_calls']} "
              f"| {r['in_tokens']} | {r['out_tokens']} | {r['cost_usd']:.8f} |")
    print()
    print("### 分类聚合")
    print("| 分类 | 用例数 | LLM调用 | 输入token | 输出token | 成本($) |")
    print("|---|---|---|---|---|---|")
    for cat, s in agg["by_category"].items():
        print(f"| {cat} | {s['cases']} | {s['llm_calls']} | {s['in_tokens']} "
              f"| {s['out_tokens']} | {s['cost_usd']:.8f} |")

    if args.output:
        payload = {
            "params": {"chars_per_token": args.chars_per_token,
                       "price_per_1k": args.price_per_1k},
            "aggregate": agg,
            "results": results,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n预演结果已写入: {args.output}")


if __name__ == "__main__":
    main()
