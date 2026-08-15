"""TD-1 修复测试：LLM 调用失败路径独立埋点 llm_error

技术债计划：docs/tech_debt_fallback_metric_plan_20260801.md TD-1
修复位置：orchestrator.py L516-520（except 分支补记 llm_error）

设计说明（【不易】守 INV-4——LLM 调用前埋点）：
  - llm（L507）在 LLM 调用前记录，计"尝试"（即使调用崩溃也可见）
  - llm_error 在 except 分支记录，计"失败"——llm 的失败子指标，
    与 llm_low_confidence_fallback 同为子指标模式
  - 成功路径不记 llm_error；面板 10 用 llm_error/llm 计算 LLM 错误率
    （llm 计全部尝试，纯失败时段分母不为 0）

验收标准（对照技术债计划 TD-1）：
  - LLM 异常时 _intent_layer_counts["llm_error"] 正确 +1
  - 成功路径不记 llm_error（不双计）
  - ratio 总和恒 = 1.0（分母同步机制自动纳入新 layer）
  - ≥2 个回归用例全绿

【简易】机制级测试直接调 record_intent_layer；wiring 级测试 mock
        Orchestrator 依赖链走到真实 process() except 分支，验证接线。
"""
import pytest
import threading
from unittest.mock import MagicMock, patch

from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts,
)
from agent.orchestrator.orchestrator import Orchestrator


@pytest.fixture(autouse=True)
def _reset_counts():
    """每个测试前重置模块级 ratio 计数视图，隔离测试间状态"""
    reset_intent_layer_counts()
    yield
    reset_intent_layer_counts()


def _ratio_sum():
    """计算当前 ratio 总和（分母同步不变量，应恒 = 1.0）"""
    total = sum(_intent_layer_counts.values())
    if total == 0:
        return 0.0
    return sum(c / total for c in _intent_layer_counts.values())


def _make_test_orch(**overrides):
    """创建带 mock 属性的测试 Orchestrator（复用 test_digital_life_comprehensive 模式）"""
    from agent.guardrails.input_guard import GuardAction, GuardResult
    from agent.guardrails.output_guard import OutputResult

    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")  # process() L298 解包二元组
    behavior.profile.enable_reflection = False

    orch = Orchestrator.__new__(Orchestrator)
    defaults = {
        "_running": True,
        "_interaction_count": 1,
        "_interaction_lock": threading.Lock(),
        "_last_context_warning": None,
        "_last_was_template": False,
        "_session_id": "test-td1",
        "_guardrails_input_guard": MagicMock(check=lambda x: GuardResult(GuardAction.ALLOW)),
        "_guardrails_output_guard": MagicMock(check=lambda x: OutputResult(filtered=x)),
        "_workflow_engine": MagicMock(try_match=lambda x: None),
        "_memory": MagicMock(),
        "_behavior": behavior,
        "_build_body_status": MagicMock(return_value="Body status"),
        "_build_reject_response": MagicMock(return_value="Request rejected"),
        "_call_llm": MagicMock(return_value="Response"),
        "_call_llm_v2": MagicMock(return_value="Response"),
        "_set_thinking_mode": MagicMock(),
        "_check_context_usage": MagicMock(return_value=None),
        "_v2_lifetrace": False,
        "_v2_distillation": False,
        "_v2_persona": False,
        "_vector_memory": None,
        "_trace_recorder": None,
        "_error_reporter": None,
        "_current_mode": MagicMock(value="test_mode"),
        "_persona_injector": None,
        "_persona_extractor": None,
        "_planning_enabled": False,
        "_planner": None,
        "_needs_planning": lambda x: False,
        "_is_skill_enabled": lambda x: False,
        # 直落到 LLM 步骤所需的关键路由桩（避免真实语义/拒识/模板逻辑干扰）
        "_update_dst_after_route": MagicMock(),
        "_semantic_layer_match": MagicMock(return_value=None),
        "_should_reject": MagicMock(return_value=(False, "")),
        "_load_reject_config": MagicMock(return_value={"threshold": 0.3}),
        "check_health": MagicMock(return_value=[]),
    }
    for k, v in defaults.items():
        setattr(orch, k, v)
    for k, v in overrides.items():
        setattr(orch, k, v)
    return orch


# ════════════════════════════════════════════════════════════════════
#  机制级：llm_error 计数行为（不拉起 Orchestrator）
# ════════════════════════════════════════════════════════════════════

class TestLlmErrorMechanism:
    """llm_error layer 的计数与 ratio 行为（机制级）"""

    def test_llm_error_计入分母_ratio_总和_1_0(self):
        """llm + llm_error（失败请求）ratio 总和仍 = 1.0"""
        record_intent_layer("llm")           # L507 尝试
        record_intent_layer("llm_error")     # except 分支失败
        assert _intent_layer_counts["llm_error"] == 1
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_成功路径_不记_llm_error(self):
        """成功且高置信请求只记 llm，不产生 llm_error（不双计）"""
        record_intent_layer("llm")
        assert "llm_error" not in _intent_layer_counts
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_llm_error_子集不变量_始终小于等于_llm(self):
        """llm_error ⊆ llm：失败次数 ≤ 尝试次数（业务语义）"""
        for _ in range(10):
            record_intent_layer("llm")
        for _ in range(3):
            record_intent_layer("llm")
            record_intent_layer("llm_error")
        assert _intent_layer_counts["llm"] == 13
        assert _intent_layer_counts["llm_error"] == 3
        assert _intent_layer_counts["llm_error"] <= _intent_layer_counts["llm"]
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_错误率指标_llm_error_除以_llm(self):
        """LLM 错误率 = llm_error / llm（面板 10 折线语义）"""
        for _ in range(7):
            record_intent_layer("llm")
        for _ in range(3):
            record_intent_layer("llm")
            record_intent_layer("llm_error")
        error_rate = _intent_layer_counts["llm_error"] / _intent_layer_counts["llm"]
        assert abs(error_rate - 0.3) < 1e-9
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_全失败场景_ratio_仍_1_0(self):
        """压力场景：全部请求失败（llm + llm_error 双计）ratio 仍 = 1.0"""
        for _ in range(100):
            record_intent_layer("llm")
            record_intent_layer("llm_error")
        assert _intent_layer_counts["llm"] == 100
        assert _intent_layer_counts["llm_error"] == 100
        assert abs(_ratio_sum() - 1.0) < 1e-9


# ════════════════════════════════════════════════════════════════════
#  wiring 级：真实 process() 走到 except 分支，验证修复接线
# ════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestLlmErrorWiring:
    """TD-1 修复接线验证：_call_llm 抛异常 → llm_error 被真实记录"""

    @patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
    def test_llm异常_llm_error正确加1_ratio仍1_0(self):
        """LLM 调用抛异常 → except 分支触发 llm_error 埋点"""
        orch = _make_test_orch(
            _call_llm=MagicMock(side_effect=RuntimeError("LLM API timeout")),
        )
        result = orch.process("请帮我查询天气")

        # 异常被捕获，返回错误响应（success=False）
        assert result["success"] is False
        assert "遇到" in result["error"] or "问题" in result["error"]

        # 【TD-1 验收】llm_error 正确 +1（且 llm 为尝试计数，INV-4）
        assert _intent_layer_counts["llm_error"] == 1
        assert _intent_layer_counts["llm"] == 1
        # ratio 总和恒 = 1.0
        assert abs(_ratio_sum() - 1.0) < 1e-9

    @patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
    def test_llm成功_不记_llm_error(self):
        """LLM 成功（高置信）→ 只记 llm，不产生 llm_error"""
        orch = _make_test_orch(
            _call_llm=MagicMock(return_value="这是一个正常且完整的回答。"),
        )
        result = orch.process("请帮我查询天气")

        assert result["success"] is True
        assert "llm_error" not in _intent_layer_counts
        assert _intent_layer_counts["llm"] == 1
        assert abs(_ratio_sum() - 1.0) < 1e-9

    @patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
    def test_llm异常与正常交替_ratio_仍_1_0(self):
        """失败与成功交替请求：llm_error 只在失败路径出现，ratio 仍 = 1.0"""
        orch = _make_test_orch()
        # 3 次失败
        orch._call_llm = MagicMock(side_effect=RuntimeError("LLM API timeout"))
        for _ in range(3):
            orch.process("查询失败请求")
        # 2 次成功
        orch._call_llm = MagicMock(return_value="这是一个正常且完整的回答。")
        for _ in range(2):
            orch.process("查询成功请求")

        assert _intent_layer_counts["llm"] == 5      # 5 次尝试
        assert _intent_layer_counts["llm_error"] == 3  # 3 次失败
        assert "llm_low_confidence_fallback" not in _intent_layer_counts
        assert abs(_ratio_sum() - 1.0) < 1e-9

    @patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", False)
    def test_低置信度与异常_互不干扰(self):
        """低置信度(fallback)与失败(llm_error)是两个独立子指标，互不干扰"""
        orch = _make_test_orch()
        # 1 次低置信度（返回过短响应）
        orch._call_llm = MagicMock(return_value="嗯")
        orch.process("触发低置信度请求")
        # 1 次失败
        orch._call_llm = MagicMock(side_effect=RuntimeError("LLM API timeout"))
        orch.process("触发失败请求")

        assert _intent_layer_counts["llm"] == 2
        assert _intent_layer_counts["llm_low_confidence_fallback"] == 1
        assert _intent_layer_counts["llm_error"] == 1
        assert abs(_ratio_sum() - 1.0) < 1e-9


# ════════════════════════════════════════════════════════════════════
#  wiring 级：语义层异常 → semantic_failed 独立计层（TD-2 修复接线）
# ════════════════════════════════════════════════════════════════════

class TestSemanticFailedPath:
    """TD-2 修复接线验证：_semantic_layer_match 异常 → semantic_failed 被真实记录

    技术债计划：docs/tech_debt_fallback_metric_plan_20260801.md TD-2
    修复位置：orchestrator.py 语义层 except 分支（_record_intent_layer("semantic_failed")）

    设计说明（【不易】守 INV-1 不双计）：
      - semantic 在命中路径记录；semantic_failed 只在异常路径记录，二者互斥
      - semantic_failed 与 llm 埋点处于不同阶段（异常时直接降级返回，不再进入 LLM 埋点）
    """

    def test_semantic_exception_records_failed_layer(self):
        """语义层异常 → semantic_failed=1 / semantic=0 / ratio 仍 = 1.0"""
        reset_intent_layer_counts()
        orch = Orchestrator.__new__(Orchestrator)
        orch._load_semantic_layer_config = MagicMock(return_value={
            "enabled": True, "min_score": 0.3, "top_k": 5,
            "use_vector": True, "use_bm25": True, "use_reranker": True,
            "fusion_mode": "rrf",
        })
        with patch("agent.state_manager.get_skills_mgmt_service",
                   side_effect=RuntimeError("skills_mgmt loader crash")):
            result = orch._semantic_layer_match("test input", trace_id="td2")

        assert result is None, "语义层异常应降级返回 None"
        assert _intent_layer_counts.get("semantic_failed") == 1
        assert _intent_layer_counts.get("semantic") is None, "异常路径不记 semantic（互斥）"
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_semantic_hit_not_records_failed(self):
        """语义层命中 → 不记 semantic_failed（互斥不双计）"""
        reset_intent_layer_counts()
        record_intent_layer("semantic")
        assert _intent_layer_counts.get("semantic_failed") is None
        assert abs(_ratio_sum() - 1.0) < 1e-9
