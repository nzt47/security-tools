# -*- coding: utf-8 -*-
"""TASK-S10-03 现象A 回归锚：上下文预算告警的「口径」与「不得污染正式回答」

真机现象（`docs/zh/CloudPivot_v7.2重构计划/TASK-S9-01_验收报告.md` §5.1 原始输出）::

    [response] "5\\n\\n---\\n💡 **当前会话上下文即将耗尽**（已使用 37%）。..."

根因（代码行级，修复前）:
    `agent/orchestrator/orchestrator.py:1629-1647` 只要
    ``_last_context_warning["level"] == "critical"`` 就往 ``response`` **尾部拼**一段
    硬编码文案，且其中的百分比取 ``_last_context_warning["pct"]``：
      · "critical" 有**两个互相独立**的成因：``compress_rounds >= 5``（摘要退化，
        `orchestrator.py:2891-2900`）与 ``pct >= 95``（`orchestrator.py:2911-2917`）；
      · 成因是「压缩 5 次」时，``pct`` 仍被拼成「已使用 27%」，
        于是出现「即将耗尽（已使用 27%）」这条自相矛盾的读数；
      · 拼进 ``response`` 之后会被 `plugins/chat.py:267-272` 原样写入会话历史
        ⇒ 告警文本回流进后续上下文（自我污染）。

本文件是**回归锚**：修复前应失败，修复后应通过。断言按「意图」而非「字面」写：
    意图1  正式回答里不得出现告警文本（告警走结构化字段 metadata.context_notice）
    意图2  告警的读数必须自洽：触发原因是摘要退化时不得说「即将耗尽」
    意图3  告警必须披露真实上限及其来源（不是 chat.py 的显示公式 4096）
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.orchestrator.orchestrator import Orchestrator  # noqa: E402
from agent.guardrails.input_guard import GuardAction  # noqa: E402


#: 真机读数形态：会话累计 ≈ 27.0%（分母 = 真实窗口上限 131072）
REAL_LIMIT = 131072
REAL_USED_TOKENS = int(REAL_LIMIT * 0.27)   # 35389 → 27.0%

#: 修复前拼进 response 的文案片段（真机原文）
LEGACY_WARNING_TEXT = "当前会话上下文即将耗尽"
LEGACY_HINT_TEXT = "点击下方「创建新会话」按钮"


def _ConfidenceLow():
    from agent.response_workflows import Confidence
    return Confidence.LOW


def _make_orchestrator(*, compress_rounds: int, used_tokens: int,
                       limit: int = REAL_LIMIT):
    """最小化 Orchestrator（`__new__` 绕过 LifecycleManager 重型初始化）。

    本套件**不 patch** ``_check_context_usage``：现象A 的被测对象正是它。
    """
    orch = Orchestrator.__new__(Orchestrator)

    orch._running = True
    orch._interaction_count = 0
    orch._interaction_lock = threading.Lock()
    orch._session_id = "test_session"
    orch._last_was_template = False
    orch._last_context_warning = None
    orch._ctx_usage_last_check = None
    orch._current_tool_steps = []
    orch._semantic_matched_skills = []
    orch._memory_token_limit = limit
    orch._planning_enabled = False
    orch._planner = None
    orch._vector_memory = None
    orch._tool_calling_service = None
    orch._model_router = None
    orch._llm_pro = None
    orch._distillation_interval = 10
    orch._memory = MagicMock()

    orch._v2_lifetrace = None
    orch._v2_persona = None
    orch._v2_distillation = None
    orch._trace_recorder = None
    orch._persona_extractor = None
    orch._persona_injector = None
    orch._injector = None

    orch._memory.score_and_save_message = MagicMock()
    orch._memory.save_log = MagicMock()
    orch._memory.add_message = MagicMock()
    orch._memory.infer_working_memory = MagicMock()
    # get_context 返回非空（否则 _check_context_usage 直接 None）
    orch._memory.get_context = MagicMock(
        return_value=[{"role": "user", "content": "你好"}])
    orch._memory.load_summary = MagicMock(return_value=(None, None))
    orch._memory.get_working_memory = MagicMock(return_value={})
    orch._memory.get_budget_context = MagicMock(return_value=[])
    orch._memory._token_counter = MagicMock()
    orch._memory._token_counter.count = MagicMock(return_value=100)
    orch._memory._token_counter.count_messages = MagicMock(return_value=used_tokens)
    orch._memory._storage = MagicMock()
    orch._memory._storage.load_recent_messages = MagicMock(return_value=[])
    orch._memory.compress_rounds = compress_rounds

    orch._workflow_engine = MagicMock()
    orch._workflow_engine.try_match = MagicMock(return_value=MagicMock(
        matched=False, output="", intent="", confidence=0.0,
        execution_time_ms=0.0, rule_name="", data=None))

    orch._behavior = MagicMock()
    orch._behavior.can_execute = MagicMock(return_value=(True, ""))
    orch._behavior.profile = MagicMock()
    orch._behavior.profile.label = "default"
    orch._behavior.profile.description = "test"
    orch._behavior.profile.enable_reflection = False
    orch._behavior.profile.response_prefix = ""
    orch._behavior.evaluate = MagicMock(return_value=MagicMock(value="normal"))

    orch._llm = None
    orch._current_mode = MagicMock()
    orch._current_mode.value = "normal"

    orch._guardrails_input_guard = MagicMock()
    orch._guardrails_input_guard.check = MagicMock(
        return_value=MagicMock(action=GuardAction.ALLOW, reason="",
                               matched_pattern=""))
    orch._guardrails_output_guard = MagicMock()
    orch._guardrails_output_guard.check = MagicMock(
        return_value=MagicMock(modified=False, redacted_fields=[], filtered=""))

    orch.check_health = MagicMock(return_value=[])
    orch._build_body_status = MagicMock(return_value="")
    orch._build_tool_status_text = MagicMock(return_value="")
    orch._build_skill_instructions = MagicMock(return_value="")
    orch._build_offline_response = MagicMock(return_value="OFFLINE_RESPONSE")
    orch._set_thinking_mode = MagicMock()
    orch._is_skill_enabled = MagicMock(return_value=False)
    orch._get_enabled_tools_whitelist = MagicMock(return_value=[])
    orch._is_smart_tool_selection_enabled = MagicMock(return_value=False)
    orch._select_model_for_request = MagicMock(return_value=(None, "mock-model"))
    orch._build_reject_response = MagicMock(return_value="REJECT_RESPONSE")
    orch._should_reject = MagicMock(return_value=(False, "test: allow"))
    orch._run_persona_distillation = MagicMock()
    orch._guard_llm_output = MagicMock(side_effect=lambda resp, *a, **kw: resp)
    orch._context_assembler_extra = MagicMock(return_value="")
    orch._get_lifetrace_context = MagicMock(return_value="")
    orch._learn_workflow_from_interaction = MagicMock(return_value=False)

    orch._mock_skills_service = MagicMock()
    orch._mock_skills_service.loader = MagicMock()
    orch._mock_skills_service.loader.match = MagicMock(return_value=None)
    return orch


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch):
    """隔离 Trace / 埋点 / 语义层配置：本套件不得写任何运行时台账。"""
    import agent.orchestrator.orchestrator as _mod

    monkeypatch.setattr(_mod, "_MONITORING_AVAILABLE", False, raising=False)
    monkeypatch.setattr(_mod, "trace_store", MagicMock(), raising=False)
    monkeypatch.setattr(_mod, "_emit_learning_metric",
                        lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(
        Orchestrator, "_load_semantic_layer_config",
        classmethod(lambda cls: {
            "enabled": False, "min_score": 0.3, "top_k": 5,
            "use_vector": False, "use_bm25": False,
            "use_reranker": False, "fusion_mode": "none",
        }))
    yield


def _process_ctx(mock_svc):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent.state_manager.get_skills_mgmt_service",
                              return_value=mock_svc))
    stack.enter_context(patch("agent.response_workflows.IntentRouter.classify",
                              return_value=("unknown", _ConfidenceLow())))
    stack.enter_context(patch("agent.response_workflows.ResponseTemplates.for_intent",
                              return_value=None))
    stack.enter_context(patch(
        "agent.orchestrator.message_handler.MessageHandler.is_follow_up",
        return_value=False))
    stack.enter_context(patch(
        "agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
        return_value=False))
    stack.enter_context(patch(
        "agent.orchestrator.message_handler.MessageHandler.extract_keywords",
        return_value=[]))
    stack.enter_context(patch(
        "agent.orchestrator.dialog_state.get_dialog_state",
        return_value=MagicMock(last_keywords=None,
                               resolve=MagicMock(return_value=None))))
    return stack


def _run_process(orch, text, *, answer="5", session_id="sess_ctx"):
    llm_calls = []
    orch._call_llm = MagicMock(
        side_effect=lambda *a, **kw: llm_calls.append(1) or answer)
    with _process_ctx(orch._mock_skills_service):
        result = orch.process(text, session_id=session_id)
    return result, llm_calls


# ═══════════════════════════════════════════════════════════════
#  意图1：告警文本不得混进正式回答
# ═══════════════════════════════════════════════════════════════

class Test告警不得污染正式回答:

    def test_压缩退化告警_不得拼进response尾部(self):
        """★ 真机锚：response 原样应为 "5"，尾部不得出现告警文案"""
        orch = _make_orchestrator(compress_rounds=5, used_tokens=REAL_USED_TOKENS)
        result, _ = _run_process(orch, "2 加 3 等于多少？只回答数字")

        text = result.get("data") or result.get("response") or ""
        assert text == "5", (
            "正式回答必须原样为 LLM 输出，不得被追加告警文本；实际=%r" % (text,))
        assert LEGACY_WARNING_TEXT not in text, "告警文本不得混进正式回答"
        assert LEGACY_HINT_TEXT not in text, "告警提示不得混进正式回答"

    def test_告警仍可见_但走结构化字段(self):
        """意图1 的正向断言：移出 response ≠ 丢掉告警（不得假绿）"""
        orch = _make_orchestrator(compress_rounds=5, used_tokens=REAL_USED_TOKENS)
        result, _ = _run_process(orch, "2 加 3 等于多少？只回答数字")

        notice = (result.get("metadata") or {}).get("context_notice")
        assert isinstance(notice, dict), (
            "告警必须以结构化系统提示暴露在 metadata.context_notice，"
            "否则是「把告警藏起来」而非「不污染回答」；实际 metadata=%r"
            % (result.get("metadata"),))
        assert notice.get("level") == "critical"
        assert notice.get("message"), "结构化告警必须带可读文案"

    def test_LLM回答里恰好含有告警字样时_不得改写回答(self):
        """反向守卫：不得用「删掉告警字样」的方式让意图1 假绿"""
        orch = _make_orchestrator(compress_rounds=5, used_tokens=REAL_USED_TOKENS)
        answer = "当前会话上下文即将耗尽 —— 这是用户自己写的一句话"
        result, _ = _run_process(orch, "复述这句话", answer=answer)
        text = result.get("data") or result.get("response") or ""
        assert text == answer, (
            "用户/模型自身的文本不得被告警逻辑改写；实际=%r" % (text,))


# ═══════════════════════════════════════════════════════════════
#  意图2：读数自洽（触发原因 vs 百分比不得互相矛盾）
# ═══════════════════════════════════════════════════════════════

class Test告警口径自洽:

    def test_压缩退化触发_不得声称即将耗尽(self):
        """compress_rounds>=5 且 pct=27% ⇒ 不得出现「即将耗尽」"""
        orch = _make_orchestrator(compress_rounds=5, used_tokens=REAL_USED_TOKENS)
        warn = orch._check_context_usage()

        assert warn is not None and warn["level"] == "critical"
        assert warn.get("reason") == "summary_degraded", (
            "触发原因必须显式声称为摘要退化（压缩轮次），实际 reason=%r"
            % (warn.get("reason"),))
        assert "即将耗尽" not in warn["message"], (
            "触发原因是摘要退化（27%%），文案不得声称上下文即将耗尽；实际=%r"
            % (warn["message"],))
        assert "已压缩" in warn["message"] and "5" in warn["message"], (
            "文案必须说清真实成因（已压缩 5 次）；实际=%r" % (warn["message"],))

    def test_真实高占用触发_才可声称即将耗尽(self):
        """pct>=95（compress_rounds=0）⇒ 文案可以且应当说「即将耗尽」"""
        orch = _make_orchestrator(compress_rounds=0,
                                  used_tokens=int(REAL_LIMIT * 0.97))
        warn = orch._check_context_usage()

        assert warn is not None and warn["level"] == "critical"
        assert warn.get("reason") == "usage_high", (
            "高占用触发必须声称为 usage_high，实际 reason=%r" % (warn.get("reason"),))
        assert "即将耗尽" in warn["message"]

    def test_告警披露真实上限与来源(self):
        """意图3：分母必须是真实窗口上限（131072）并披露来源，而非显示公式 4096"""
        orch = _make_orchestrator(compress_rounds=5, used_tokens=REAL_USED_TOKENS)
        warn = orch._check_context_usage()

        assert warn["limit_tokens"] == REAL_LIMIT, (
            "告警分母必须是真实窗口上限；实际 limit_tokens=%r" % (warn.get("limit_tokens"),))
        assert warn["limit_tokens"] != 4096, (
            "不得使用 plugins/chat.py 显示公式里的硬编码 4096")
        assert str(REAL_LIMIT) in warn["message"], (
            "文案必须披露真实上限；实际=%r" % (warn["message"],))
        assert warn.get("limit_source"), (
            "必须声明上限来源（配置键 / 环境变量 / 内置默认值），否则读数不可复核")
        assert warn["used_tokens"] == REAL_USED_TOKENS

    def test_低占用且未压缩_不产生告警(self):
        """pct<60 且 compress_rounds<3 ⇒ 无告警（不得无端打扰）"""
        orch = _make_orchestrator(compress_rounds=0,
                                  used_tokens=int(REAL_LIMIT * 0.27))
        assert orch._check_context_usage() is None
