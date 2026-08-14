"""模拟失败反思闭环（权限拒绝 + 网络超时双场景），验证结构化诊断 + 修复建议正确注入

场景一：权限拒绝 — submit_report 抛 PermissionError（message 含"权限"）
场景二：网络超时 — fetch_order_data 抛 ConnectionError（message 含"网络请求超时"）
两场景在**同一脚本**内各自完成失败反思闭环，验证混合异常下的反思鲁棒性：

  1. build_diagnosis 错误分类正确（permission_denied / network_timeout），
     repair_hints 对应"检查权限声明与工具白名单" / "建议重试或换备用路径"
  2. reflector.failure_reflect 产出 root_cause/repair_actions/avoid 并注入 _hints
  3. 失败历史注入 _failure_history（含根因猜测）
  4. 第 2 轮 _think prompt 含失败反思记录（强制换思路）
  5. 失败教训沉淀 lessons.json 且磁盘持久化
  6. 【重新加载恢复】新 Reflector 实例读同一目录，lessons_db 恢复
  7. get_advice_for_task 能检索到对应失败教训

运行：python scripts/simulate_permission_denied_reflection.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector
from planning.diagnostics import build_diagnosis
from planning.models.action import ActionResult


def submit_report(report: str = "Q3 营收"):
    """模拟权限拒绝：服务账号缺 report:write 权限"""
    raise PermissionError("权限不足: 服务账号无 report:write 权限，请检查权限声明与工具白名单")


def fetch_order_data(order_id: str = "A001"):
    """模拟网络超时：连接上游 API 网关失败"""
    raise ConnectionError("模拟网络请求超时: 连接 api.example.com 超时（上游网关不可达）")


class FakeLLM:
    """按 prompt 内容分发：思考 prompt 走思考序列、反思 prompt 返回反思 JSON"""

    def __init__(self, think_responses, reflect_response):
        self._think_responses = list(think_responses)
        self._reflect_response = reflect_response
        self.prompts = []

    async def chat(self, messages):
        content = messages[0]["content"]
        self.prompts.append(content)
        if "反思引擎" in content:
            return self._reflect_response
        if self._think_responses:
            return self._think_responses.pop(0)
        return json.dumps({"reasoning": "完成", "action_type": "finish", "result": "任务结束"})


async def _run_scenario(label, task_desc, tool_name, tool_fn, think_responses, reflect_response,
                        expect_type, hint_frag, diag_frags, prompt_marker, advice_marker):
    """运行单个失败反思场景并输出验证结果，返回是否全部通过"""
    print("=" * 80)
    print(f"【场景】{label} → 结构化诊断 + 修复建议注入验证")
    print("=" * 80)

    fake_llm = FakeLLM(think_responses, reflect_response)
    planner = type("P", (), {})()
    planner.llm = fake_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register(tool_name, tool_fn)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        context: dict = {"session": f"{label}_demo"}
        result = await loop.run(task_desc, context)

        print("─" * 80)
        print("【验证结果】")
        failed_steps = [s for s in result.steps if not s.success]
        hist = context.get("_failure_history") or []
        last = hist[-1] if hist else {}
        hints = context.get("_hints") or []

        # 结构化诊断直接验证：build_diagnosis 对错误给出 error_type + repair_hints
        # （教训：_failure_history 条目不含 repair_hints，必须直接检查诊断产物）
        diag = build_diagnosis(
            ActionResult.failure_result(f"工具执行失败: {last.get('error', '') or label}"),
            attempts=1,
            tool_name=tool_name,
        )
        diag_ok = (
            diag.error_type == expect_type
            and all(any(frag in h for h in diag.repair_hints) for frag in diag_frags)
        )

        # 重新加载恢复验证：新实例读同一 persist_dir，失败经验应能从磁盘恢复
        reloaded = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        reloaded_lessons = len(reloaded.lessons_db)
        reloaded_ok = (
            reloaded_lessons >= 1
            and any("根因" in l.failure_point for l in reloaded.lessons_db)
        )
        advice = reflector.get_advice_for_task(task_desc)

        checks = [
            (f"history 记录 error_type={expect_type}",
             last.get("error_type") == expect_type),
            (f"build_diagnosis 分类 {expect_type} + repair_hints 匹配", diag_ok),
            (f"修复建议注入 _hints（{hint_frag} 方向）",
             any(hint_frag in h for h in hints)),
            ("失败历史注入 _failure_history（含根因猜测）", bool(last.get("guess"))),
            (f"第 2 轮 prompt 含失败反思记录（{prompt_marker}）",
             any("失败反思记录" in p and prompt_marker in p for p in fake_llm.prompts)),
            ("lessons.json 持久化成功",
             os.path.exists(os.path.join(tmp_dir, "lessons.json"))),
            ("重新加载后 lessons_db 恢复（落盘可复用）", reloaded_ok),
            (f"get_advice_for_task 检索到 {label} 失败教训",
             advice is not None
             and advice.get("related_lessons", 0) >= 1
             and any(advice_marker in p.get("failure", "")
                     for p in advice.get("common_pitfalls") or [])),
        ]
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")

        print(f"  失败步骤数={len(failed_steps)} | 反思次数={len(hist)} | 重新加载 lessons={reloaded_lessons}")
        print("─" * 80)
        print(f"【第 2 轮 prompt {label} 反思注入段】")
        for p in fake_llm.prompts:
            if "失败反思记录" in p and prompt_marker in p:
                start = p.index("【失败反思记录")
                print(p[start:start + 300])
                break

        print("─" * 80)
        print("【get_advice_for_task 检索结果】")
        if advice:
            for pit in advice.get("common_pitfalls") or []:
                print(f"  - {pit['failure'][:140]}")
        else:
            print("  （无检索结果）")

        ok = all(ok for _, ok in checks)
        print(f"总体: {'PASS' if ok else 'FAIL'} — {label} 诊断+修复建议闭环正确注入且落盘")
        return ok


async def main() -> int:
    results = []

    # 场景一：权限拒绝
    results.append(await _run_scenario(
        label="权限拒绝",
        task_desc="提交 Q3 营收报告",
        tool_name="submit_report",
        tool_fn=submit_report,
        think_responses=[
            json.dumps({"reasoning": "调用提交报告接口", "action_type": "tool_call",
                        "action": {"tool": "submit_report", "params": {"report": "Q3 营收"},
                                   "description": "提交 Q3 营收报告"}}),
            json.dumps({"reasoning": "按反思建议申请权限后重试", "action_type": "tool_call",
                        "action": {"tool": "submit_report", "params": {"report": "Q3 营收"},
                                   "description": "重试提交报告"}}),
            json.dumps({"reasoning": "权限未批准，任务终止", "action_type": "finish",
                        "result": "无权限，放弃"}),
        ],
        reflect_response=json.dumps({
            "root_cause": "服务账号缺少 report:write 权限，工具白名单未包含该操作",
            "confidence": 0.9,
            "repair_actions": ["向管理员申请 report:write 权限", "改用只读接口(api-ro.example.com)"],
            "avoid": ["重复尝试同一提交接口", "伪造权限声明"],
        }),
        expect_type="permission_denied",
        hint_frag="report:write 权限",
        diag_frags=("检查权限声明与工具白名单", "勿重复尝试"),
        prompt_marker="权限",
        advice_marker="权限",
    ))

    # 场景二：网络超时
    results.append(await _run_scenario(
        label="网络超时",
        task_desc="获取订单 A001 数据",
        tool_name="fetch_order_data",
        tool_fn=fetch_order_data,
        think_responses=[
            json.dumps({"reasoning": "调用订单查询接口", "action_type": "tool_call",
                        "action": {"tool": "fetch_order_data", "params": {"order_id": "A001"},
                                   "description": "获取订单 A001 数据"}}),
            json.dumps({"reasoning": "按反思建议切换备用接口重试", "action_type": "tool_call",
                        "action": {"tool": "fetch_order_data", "params": {"order_id": "A001"},
                                   "description": "重试获取订单"}}),
            json.dumps({"reasoning": "备用路径仍超时，任务终止", "action_type": "finish",
                        "result": "网络不可达，放弃"}),
        ],
        reflect_response=json.dumps({
            "root_cause": "上游 API 网关超时，网络不可达",
            "confidence": 0.85,
            "repair_actions": ["改用备用接口(api-backup.example.com)", "降低超时重试次数"],
            "avoid": ["无限重试同一接口", "等待更长超时"],
        }),
        expect_type="network_timeout",
        hint_frag="备用接口",
        diag_frags=("建议重试或换备用路径", "禁止无限重试"),
        prompt_marker="网络",
        advice_marker="网络",
    ))

    all_ok = all(results)
    print("=" * 80)
    print(f"总体: {'PASS — 混合异常（权限拒绝+网络超时）反思闭环全部正确注入且落盘' if all_ok else 'FAIL'}")
    print("=" * 80)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
