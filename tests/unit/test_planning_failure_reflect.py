"""任务4：失败路径反思闭环 — 失败反思/结构化诊断单元测试

覆盖：
1. diagnostics.build_diagnosis 错误分类与修复提示（D12 诊断表，含中文/英文特征）
2. reflector.failure_reflect 规则兜底（LLM 不可用/异常/输出非法）
3. reflector.failure_reflect LLM 解析与失败教训沉淀（复用 learn_from_experience 失败分支）
4. ReActLoop 失败反思接线：失败步骤后 failure_reflect 被调用、修复建议注入
5. 反思收敛：reflection_retries 达上限后停止失败反思
"""
import json
import os
import tempfile

import pytest
from unittest.mock import AsyncMock

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector
from planning.diagnostics import (
    FailureDiagnosis,
    build_diagnosis,
    TOOL_NOT_FOUND,
    MAX_ERROR_MESSAGE,
)
from planning.models.action import ActionResult
from planning.models import Task


# ── 诊断错误分类与修复提示（D12 诊断表）───────────────────────────────────────


class TestDiagnosis:
    def test_classify_timeout_zh(self):
        assert classify("请求超时") == "network_timeout"

    def test_classify_timeout_en(self):
        assert classify("Connection timed out") == "network_timeout"

    def test_classify_permission(self):
        assert classify("权限不足") == "permission_denied"
        assert classify("Permission denied") == "permission_denied"

    def test_classify_tool_not_found(self):
        assert classify("工具不存在: search_tool") == TOOL_NOT_FOUND
        assert classify("Tool 'abc' not found") == TOOL_NOT_FOUND

    def test_classify_external_api(self):
        assert classify("API 调用失败: 400") == "external_api"

    def test_classify_unknown(self):
        assert classify("boom") == "unknown"

    def test_build_diagnosis_hints_timeout(self):
        diag = build_diagnosis(ActionResult.failure_result("请求超时"), attempts=1)
        assert diag.error_type == "network_timeout"
        assert any("禁止无限重试" in h for h in diag.repair_hints)

    def test_build_diagnosis_hints_tool_not_found(self):
        diag = build_diagnosis(
            ActionResult.failure_result("工具不存在: x"), attempts=1, tool_name="x"
        )
        assert diag.error_type == TOOL_NOT_FOUND
        assert any("勿虚构工具" in h for h in diag.repair_hints)

    def test_build_diagnosis_hints_permission(self):
        diag = build_diagnosis(ActionResult.failure_result("权限不足"), attempts=1)
        assert any("勿重复尝试" in h for h in diag.repair_hints)

    def test_build_diagnosis_truncates_error_message(self):
        long_err = "错" * 1000
        diag = build_diagnosis(ActionResult.failure_result(long_err), attempts=1)
        assert len(diag.error_message) <= MAX_ERROR_MESSAGE

    def test_build_diagnosis_carries_attempt_history_context(self):
        diag = build_diagnosis(
            ActionResult.failure_result("boom"),
            attempts=2,
            history=[{"attempt": 1, "action": "a", "error": "e"}],
            tool_name="t1",
            project_context={"available_tools": ["t1", "t2"]},
        )
        assert diag.attempt == 2
        assert diag.history == [{"attempt": 1, "action": "a", "error": "e"}]
        assert diag.tool_name == "t1"
        # project_context 值按设计字符串化（token 预算裁剪）
        assert diag.project_context["available_tools"] == "['t1', 't2']"

    def test_to_dict_roundtrip(self):
        diag = build_diagnosis(ActionResult.failure_result("boom"), attempts=1)
        data = diag.to_dict()
        assert data["error_type"] == "unknown"
        assert data["attempt"] == 1


def classify(error_text: str) -> str:
    from planning.diagnostics import classify_error
    return classify_error(error_text)


# ── Reflector.failure_reflect（规则兜底 / LLM 解析 / 教训沉淀）──────────────


class TestFailureReflect:
    @pytest.mark.asyncio
    async def test_rule_based_when_no_llm(self):
        """无 LLM → 规则兜底：root_cause/repair_actions 来自 hints 表，教训沉淀持久化"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            task = Task(id="t1", description="运行坏工具")
            result = ActionResult.failure_result("工具执行失败: 权限不足")
            diag = build_diagnosis(result, attempts=1, tool_name="bad_tool")

            enhanced = await reflector.failure_reflect(task, result, diag, attempts=1)
            assert enhanced is not None
            assert bool(enhanced.root_cause)  # 规则兜底产出根因
            assert isinstance(enhanced.repair_actions, list) and len(enhanced.repair_actions) >= 1
            assert any("勿重复尝试" in a for a in enhanced.repair_actions)
            # 教训沉淀：lessons_db 新增且文件持久化
            assert len(reflector.lessons_db) >= 1
            assert os.path.exists(os.path.join(tmp_dir, "lessons.json"))
            # reflection_history 记录 type=failure
            assert any(e.get("type") == "failure" for e in reflector.reflection_history)

    @pytest.mark.asyncio
    async def test_llm_parses_structured_reflection(self):
        """LLM 可用 → 解析 root_cause/repair_actions/avoid"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "root_cause": "工具权限配置缺失",
            "confidence": 0.85,
            "repair_actions": ["检查权限白名单", "改用其他工具"],
            "avoid": ["重复调用原工具"],
        })
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(llm_service=mock_llm, persist_dir=tmp_dir)
            task = Task(id="t1", description="运行坏工具")
            result = ActionResult.failure_result("工具执行失败: 权限不足")
            diag = build_diagnosis(result, attempts=1, tool_name="bad_tool")

            enhanced = await reflector.failure_reflect(task, result, diag, attempts=1)
            assert enhanced.root_cause == "工具权限配置缺失"
            assert enhanced.confidence == 0.85
            assert enhanced.repair_actions == ["检查权限白名单", "改用其他工具"]
            assert enhanced.avoid == ["重复调用原工具"]

    @pytest.mark.asyncio
    async def test_llm_invalid_json_falls_back_to_rules(self):
        """LLM 输出非法 JSON → 规则兜底（不抛异常、不阻断）"""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = "not-json{{"
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(llm_service=mock_llm, persist_dir=tmp_dir)
            task = Task(id="t1", description="运行坏工具")
            result = ActionResult.failure_result("工具执行失败: boom")
            diag = build_diagnosis(result, attempts=1)

            enhanced = await reflector.failure_reflect(task, result, diag, attempts=1)
            assert enhanced is not None
            assert bool(enhanced.root_cause)  # 规则兜底
            assert len(enhanced.repair_actions) >= 1

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_rules(self):
        """LLM 抛异常 → 规则兜底（不阻断主循环）"""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = RuntimeError("llm down")
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(llm_service=mock_llm, persist_dir=tmp_dir)
            task = Task(id="t1", description="运行坏工具")
            result = ActionResult.failure_result("工具执行失败: 请求超时")
            diag = build_diagnosis(result, attempts=1)

            enhanced = await reflector.failure_reflect(task, result, diag, attempts=1)
            assert enhanced is not None
            assert enhanced.error_type == "network_timeout"

    @pytest.mark.asyncio
    async def test_lesson_dedup_appends_root_cause_version(self):
        """基础 lesson 已存在（executor 失败归因已记录）→ 追加 lesson_fail_ 带根因"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            task_desc = "运行坏工具"
            await reflector.learn_from_experience(
                task_desc, ActionResult.failure_result("工具执行失败: boom")
            )
            before = len(reflector.lessons_db)

            task = Task(id="t1", description=task_desc)
            result = ActionResult.failure_result("工具执行失败: boom")
            diag = build_diagnosis(result, attempts=1)
            enhanced = await reflector.failure_reflect(task, result, diag, attempts=1)

            assert len(reflector.lessons_db) == before + 1
            assert reflector.lessons_db[-1].id.startswith("lesson_fail_")
            assert reflector.lessons_db[-1].solution  # 带修复建议

    @pytest.mark.asyncio
    async def test_failure_reflect_stats_llm_fallback_accuracy(self):
        """混合异常路径统计准确：LLM 正常/异常/非法 JSON/无 LLM 四路触发计数与原因细分"""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            json.dumps({"root_cause": "根因A", "confidence": 0.8,
                        "repair_actions": ["修复1"], "avoid": []}),
            RuntimeError("llm down"),     # 异常 → exception 兜底
            "not-json{{",                 # 非法 JSON → parse_failed 兜底
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(llm_service=mock_llm, persist_dir=tmp_dir)
            task = Task(id="t1", description="运行坏工具")
            result = ActionResult.failure_result("工具执行失败: 权限不足")
            diag = build_diagnosis(result, attempts=1)

            await reflector.failure_reflect(task, result, diag, attempts=1)  # LLM 正常
            await reflector.failure_reflect(task, result, diag, attempts=2)  # 异常
            await reflector.failure_reflect(task, result, diag, attempts=3)  # 非法 JSON
            # 无 LLM 实例 → not_configured 兜底
            reflector_no_llm = Reflector(persist_dir=tmp_dir)
            await reflector_no_llm.failure_reflect(task, result, diag, attempts=4)

            stats = reflector._failure_reflect_stats
            assert stats["llm"] == 1, "仅首次成功走 LLM 路径"
            assert stats["fallback"] == 2, "异常+解析失败各触发一次规则兜底"
            assert stats["fallback_reasons"] == {
                "not_configured": 0, "exception": 1, "parse_failed": 1,
            }, "兜底原因细分准确"
            no_llm_stats = reflector_no_llm._failure_reflect_stats
            assert no_llm_stats["fallback"] == 1
            assert no_llm_stats["fallback_reasons"]["not_configured"] == 1


# ── ReActLoop 失败反思接线（D12 修复）────────────────────────────────────────


class _StubFailureReflector(Reflector):
    """stub：failure_reflect 返回固定增强诊断（计数），不调 LLM"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failure_reflect_calls = 0

    async def failure_reflect(self, task, result, diagnosis, attempts):
        self.failure_reflect_calls += 1
        diagnosis.root_cause = "bad_tool 根因"
        diagnosis.confidence = 0.9
        diagnosis.repair_actions = ["改用其他工具"]
        diagnosis.avoid = ["继续调用 bad_tool"]
        return diagnosis


def _bad_tool():
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_failure_step_triggers_failure_reflect_and_inject():
    """失败步骤后 failure_reflect 被调用，修复建议/失败历史注入 context"""
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "调用工具", "action_type": "tool_call",
            "action": {"tool": "bad_tool", "params": {}, "description": "运行坏工具"},
        }),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "成功"}),
    ]
    planner = type("P", (), {})()
    planner.llm = mock_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("bad_tool", _bad_tool)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = _StubFailureReflector(persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        context = {"session": "t"}
        await loop.run("运行坏工具", context)

        assert reflector.failure_reflect_calls >= 1
        # 修复建议注入 _hints（供下一轮 _think 消费）；
        # 子串匹配（与模拟脚本同款修复，避免 repair_actions 字符串变化导致精确匹配误失败）
        assert any("改用其他工具" in h for h in context.get("_hints", []))
        # 失败历史注入（带根因猜测）
        history = context.get("_failure_history", [])
        assert len(history) >= 1
        assert history[-1]["error_type"] == "unknown"
        assert history[-1]["guess"] == "bad_tool 根因"


@pytest.mark.asyncio
async def test_failure_reflect_respects_retry_limit():
    """反思收敛：reflection_retries=1 时多次失败只反思 1 次（防反思循环放大成本）"""
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "调用工具", "action_type": "tool_call",
            "action": {"tool": "bad_tool", "params": {}, "description": ""},
        }),
        json.dumps({
            "reasoning": "再试一次", "action_type": "tool_call",
            "action": {"tool": "bad_tool", "params": {}, "description": ""},
        }),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "成功"}),
    ]
    planner = type("P", (), {})()
    planner.llm = mock_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("bad_tool", _bad_tool)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = _StubFailureReflector(persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=10,
                         config={"reflection_retries": 1})
        await loop.run("运行坏工具")

        # 两次失败，但反思轮数上限为 1
        assert reflector.failure_reflect_calls == 1
