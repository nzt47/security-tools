"""任务4：失败路径反思闭环 — 权限拒绝场景集成测试（e2e）

将模拟脚本（simulate_permission_denied_reflection.py）验证逻辑沉淀为正式集成测试，
覆盖：
1. build_diagnosis 对权限错误给出 permission_denied + 对应 repair_hints
   （修复脚本断言 bug：从 _failure_history 取 repair_hints 不可靠，应直接验证诊断产物）
2. 真实 ReActLoop 权限拒绝闭环：失败反思触发、修复建议/失败历史注入、第 2 轮 prompt
   含权限失败反思记录
3. 失败经验持久化：lessons.json 落盘 + 新实例重新加载恢复 + get_advice_for_task 检索复用
"""
import json
import os
import tempfile

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector
from planning.diagnostics import build_diagnosis
from planning.models.action import ActionResult


def _permission_tool(report: str = "Q3 营收"):
    """模拟权限拒绝：服务账号缺 report:write 权限"""
    raise PermissionError("权限不足: 服务账号无 report:write 权限，请检查权限声明与工具白名单")


class _FakeLLM:
    """按 prompt 内容分发：思考 prompt 走思考序列、反思 prompt 返回反思 JSON（零网络依赖）"""

    def __init__(self, think_responses, reflect_response):
        self._think_responses = list(think_responses)
        self._reflect_response = reflect_response
        self.prompts = []

    async def chat(self, messages):
        content = messages[0]["content"]
        self.prompts.append(content)
        if "反思引擎" in content:  # FAILURE_REFLECTION_PROMPT 特征
            return self._reflect_response
        if self._think_responses:
            return self._think_responses.pop(0)
        return json.dumps({"reasoning": "完成", "action_type": "finish", "result": "任务结束"})


def test_build_diagnosis_permission_denied_repair_hints():
    """结构化诊断：权限错误 → permission_denied + 对应修复提示（断言 bug 修复逻辑）

    教训：_failure_history 条目不含 repair_hints 字段，验证修复提示必须直接检查
    build_diagnosis 产物（模拟脚本原从 history 取导致误判 FAIL）。
    """
    diag = build_diagnosis(
        ActionResult.failure_result("工具执行失败: 权限不足: 无 report:write 权限"),
        attempts=1,
        tool_name="submit_report",
    )
    assert diag.error_type == "permission_denied"
    assert any("检查权限声明与工具白名单" in h for h in diag.repair_hints)
    assert any("勿重复尝试" in h for h in diag.repair_hints)


async def test_permission_denied_reflection_e2e():
    """权限拒绝失败反思闭环（集成）：触发、注入、prompt、持久化恢复、检索复用"""
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
        json.dumps({"reasoning": "权限未批准，任务终止", "action_type": "finish",
                    "result": "无权限，放弃"}),
    ]
    reflect_response = json.dumps({
        "root_cause": "服务账号缺少 report:write 权限，工具白名单未包含该操作",
        "confidence": 0.9,
        "repair_actions": ["向管理员申请 report:write 权限", "改用只读接口(api-ro.example.com)"],
        "avoid": ["重复尝试同一提交接口", "伪造权限声明"],
    })
    fake_llm = _FakeLLM(think_responses, reflect_response)

    planner = type("P", (), {})()
    planner.llm = fake_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("submit_report", _permission_tool)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        context = {"session": "permission_denied_demo"}
        await loop.run("提交 Q3 营收报告", context)

        # 1) 失败反思触发 + 结构化诊断注入 context
        history = context.get("_failure_history", [])
        assert len(history) >= 1, "失败历史应记录反思"
        assert history[0]["attempt"] == 1, "首次失败反思 attempt 应为 1"
        assert history[-1]["attempt"] == len(history), "attempt 应随失败轮次递增"
        assert history[-1]["error_type"] == "permission_denied"
        assert bool(history[-1].get("guess")), "历史应携带根因猜测"

        # 2) 修复建议注入 _hints（勿重复尝试方向）
        assert any("report:write 权限" in h for h in context.get("_hints", []))

        # 3) 第 2 轮 prompt 含权限失败反思记录（强制换思路）
        assert any("失败反思记录" in p and "权限" in p for p in fake_llm.prompts)

        # 4) 失败经验落盘 + 重新加载恢复（持久化可复用）
        assert os.path.exists(os.path.join(tmp_dir, "lessons.json"))
        reloaded = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        assert len(reloaded.lessons_db) >= 1
        assert any("根因" in l.failure_point for l in reloaded.lessons_db)

        # 5) get_advice_for_task 检索到权限失败教训
        advice = reflector.get_advice_for_task("提交 Q3 营收报告")
        assert advice is not None
        assert advice.get("related_lessons", 0) >= 1
        assert any("权限" in p.get("failure", "") for p in advice.get("common_pitfalls") or [])
