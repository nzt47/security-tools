"""模拟权限拒绝（permission_denied）场景，验证结构化诊断 + 修复建议正确注入

场景：LLM 连续两次调用 `submit_report` 工具，工具模拟"权限不足"（PermissionError，
message 含"权限"）→ 失败反思分支应被触发并验证：
  1. build_diagnosis 将错误分类为 permission_denied，repair_hints 含
     "检查权限声明与工具白名单，勿重复尝试"
  2. reflector.failure_reflect 产出 root_cause/repair_actions/avoid 并注入 _hints
  3. 失败历史注入 _failure_history（含根因猜测）
  4. 第 2 轮 _think prompt 含权限失败反思记录（强制换思路）
  5. 失败教训沉淀 lessons.json 且磁盘持久化
  6. 【重新加载恢复】新 Reflector 实例读同一目录，lessons_db 恢复（失败经验落盘可复用）
  7. get_advice_for_task 能检索到该失败教训

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
    """模拟提交报告：服务账号缺 report:write 权限"""
    raise PermissionError("权限不足: 服务账号无 report:write 权限，请检查权限声明与工具白名单")


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


async def main() -> int:
    print("=" * 80)
    print("【模拟场景】权限拒绝 → 结构化诊断 + 修复建议注入验证")
    print("=" * 80)

    think_responses = [
        json.dumps({
            "reasoning": "调用提交报告接口",
            "action_type": "tool_call",
            "action": {"tool": "submit_report", "params": {"report": "Q3 营收"},
                       "description": "提交 Q3 营收报告"},
        }),
        json.dumps({
            "reasoning": "按反思建议申请权限后重试",
            "action_type": "tool_call",
            "action": {"tool": "submit_report", "params": {"report": "Q3 营收"},
                       "description": "重试提交报告"},
        }),
        json.dumps({"reasoning": "权限未批准，任务终止", "action_type": "finish", "result": "无权限，放弃"}),
    ]
    reflect_response = json.dumps({
        "root_cause": "服务账号缺少 report:write 权限，工具白名单未包含该操作",
        "confidence": 0.9,
        "repair_actions": ["向管理员申请 report:write 权限", "改用只读接口(api-ro.example.com)"],
        "avoid": ["重复尝试同一提交接口", "伪造权限声明"],
    })

    fake_llm = FakeLLM(think_responses, reflect_response)

    planner = type("P", (), {})()
    planner.llm = fake_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("submit_report", submit_report)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        context: dict = {"session": "permission_denied_demo"}
        result = await loop.run("提交 Q3 营收报告", context)

        print("─" * 80)
        print("【验证结果】")
        failed_steps = [s for s in result.steps if not s.success]
        hist = context.get("_failure_history") or []
        last = hist[-1] if hist else {}
        hints = context.get("_hints") or []

        # 结构化诊断直接验证：build_diagnosis 对权限错误给出 error_type + repair_hints
        diag = build_diagnosis(
            ActionResult.failure_result("工具执行失败: 权限不足: 服务账号无 report:write 权限"),
            attempts=1,
            tool_name="submit_report",
        )
        diag_ok = (
            diag.error_type == "permission_denied"
            and any("检查权限声明与工具白名单" in h for h in diag.repair_hints)
            and any("勿重复尝试" in h for h in diag.repair_hints)
        )

        # 重新加载恢复验证：新实例读同一 persist_dir，失败经验应能从磁盘恢复
        reloaded = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        reloaded_lessons = len(reloaded.lessons_db)
        reloaded_ok = (
            reloaded_lessons >= 1
            and any("根因" in l.failure_point for l in reloaded.lessons_db)
        )
        advice = reflector.get_advice_for_task("提交 Q3 营收报告")

        checks = [
            ("history 记录 error_type=permission_denied",
             last.get("error_type") == "permission_denied"),
            ("build_diagnosis 分类 permission_denied + repair_hints（权限声明/白名单/勿重复尝试）", diag_ok),
            ("修复建议注入 _hints（勿重复尝试方向）",
             any("report:write 权限" in h for h in hints)),
            ("失败历史注入 _failure_history（含根因猜测）", bool(last.get("guess"))),
            ("第 2 轮 prompt 含权限失败反思记录",
             any("失败反思记录" in p and "权限" in p for p in fake_llm.prompts)),
            ("lessons.json 持久化成功",
             os.path.exists(os.path.join(tmp_dir, "lessons.json"))),
            ("重新加载后 lessons_db 恢复（落盘可复用）", reloaded_ok),
            ("get_advice_for_task 检索到权限失败教训",
             advice is not None
             and advice.get("related_lessons", 0) >= 1
             and any("权限" in p.get("failure", "") for p in advice.get("common_pitfalls") or [])),
        ]
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")

        print(f"  失败步骤数={len(failed_steps)} | 反思次数={len(hist)} | 重新加载 lessons={reloaded_lessons}")
        print("─" * 80)
        print("【第 2 轮 prompt 权限反思注入段】")
        for p in fake_llm.prompts:
            if "失败反思记录" in p and "权限" in p:
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
        print("─" * 80)
        print(f"总体: {'PASS — 权限拒绝诊断+修复建议闭环正确注入且落盘' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
