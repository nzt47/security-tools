#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S10-03 现象A 复现/对照探针：上下文预算告警的产出点与正式回答的干净度

真机现象（`docs/zh/CloudPivot_v7.2重构计划/TASK-S9-01_验收报告.md` §5.1 原始输出）::

    [response] "5\\n\\n---\\n💡 **当前会话上下文即将耗尽**（已使用 37%）。..."

要点：这里的 37% 是 ``_memory_token_limit`` 的分母（真机未配置，落到内置默认值
131072），而 "critical" 的真实成因是 ``compress_rounds >= 5``（摘要退化）——
**与占用百分比无关**。文案把两个独立信号混成一句话，于是出现
「即将耗尽（已使用 27%）」这种自相矛盾的读数。

本探针在**进程内**复现该形态（服务进程外，不触碰 127.0.0.1:5678），打印：
  1. `_check_context_usage()` 的结构化告警（level/reason/pct/分母/来源）
  2. `process()` 返回的正式回答原样（repr）
  3. 告警是否以结构化系统提示外发（metadata.context_notice）

用法（在 worktree 根目录）:
    $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
    python scripts/dev/s1003_context_notice_repro.py
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.orchestrator.orchestrator import Orchestrator  # noqa: E402
from agent.guardrails.input_guard import GuardAction  # noqa: E402

#: 真机读数形态：真实窗口上限（未配置 memory.token_limit ⇒ 内置默认 131072）
LIMIT = 131072
#: 真机观测到的占用百分比 27% / 37%
PCT_CASES = (0.27, 0.37)
#: 真机形态下的压缩轮次（critical 的真实成因）
COMPRESS_ROUNDS = 5

WARNING_MARKERS = ("当前会话上下文即将耗尽", "创建新会话")


def _ConfidenceLow():
    from agent.response_workflows import Confidence
    return Confidence.LOW


def _make_orchestrator(*, compress_rounds: int, used_tokens: int):
    orch = Orchestrator.__new__(Orchestrator)
    orch._running = True
    orch._interaction_count = 3
    orch._interaction_lock = threading.Lock()
    orch._session_id = "s1003-probe"
    orch._last_was_template = False
    orch._last_context_warning = None
    orch._ctx_usage_last_check = None
    orch._current_tool_steps = []
    orch._semantic_matched_skills = []
    orch._memory_token_limit = LIMIT
    orch._memory_token_limit_source = "builtin_default(131072)"
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
    orch._memory.get_context = MagicMock(
        return_value=[{"role": "user", "content": "你好"}])
    orch._memory.load_summary = MagicMock(return_value=("历史摘要", 1))
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
    orch._select_model_for_request = MagicMock(return_value=(None, "probe-model"))
    orch._build_reject_response = MagicMock(return_value="REJECT_RESPONSE")
    orch._should_reject = MagicMock(return_value=(False, "probe: allow"))
    orch._run_persona_distillation = MagicMock()
    orch._guard_llm_output = MagicMock(side_effect=lambda resp, *a, **kw: resp)
    orch._context_assembler_extra = MagicMock(return_value="")
    orch._get_lifetrace_context = MagicMock(return_value="")
    orch._learn_workflow_from_interaction = MagicMock(return_value=False)
    orch._mock_skills_service = MagicMock()
    orch._mock_skills_service.loader = MagicMock()
    orch._mock_skills_service.loader.match = MagicMock(return_value=None)
    return orch


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


def main() -> int:
    import agent.orchestrator.orchestrator as _mod
    _mod._MONITORING_AVAILABLE = False
    _mod.trace_store = MagicMock()
    Orchestrator._load_semantic_layer_config = classmethod(lambda cls: {
        "enabled": False, "min_score": 0.3, "top_k": 5,
        "use_vector": False, "use_bm25": False,
        "use_reranker": False, "fusion_mode": "none",
    })

    print("=" * 78)
    print("S10-03 现象A 探针 — 告警口径 + 正式回答干净度")
    print("真机形态：compress_rounds=%d（critical 的真实成因），窗口上限 %d"
          % (COMPRESS_ROUNDS, LIMIT))
    print("=" * 78)

    verdicts = []
    for pct in PCT_CASES:
        used = int(LIMIT * pct)
        orch = _make_orchestrator(compress_rounds=COMPRESS_ROUNDS, used_tokens=used)
        warn = orch._check_context_usage()
        print("\n[pct=%.0f%%] _check_context_usage() →" % (pct * 100,))
        if warn is None:
            print("  （无告警）")
        else:
            for key in ("level", "reason", "pct", "used_tokens", "limit_tokens",
                        "limit_source", "compress_rounds"):
                print("  %-16s = %r" % (key, warn.get(key)))
            print("  message          = %r" % (warn.get("message"),))

        orch._call_llm = MagicMock(return_value="5")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("2 加 3 等于多少？只回答数字",
                                  session_id="s1003-probe-%d" % (pct * 100))
        text = result.get("data") or result.get("response") or ""
        notice = (result.get("metadata") or {}).get("context_notice")
        print("  [response]       = %r" % (text,))
        print("  [context_notice] = %r" % (notice,))
        polluted = any(marker in text for marker in WARNING_MARKERS)
        print("  [verdict] 正式回答含告警文本? %s" % (polluted,))
        print("  [verdict] 告警仍可见(结构化)? %s" % (notice is not None,))
        verdicts.append((pct, polluted, notice is not None))

    print("\n" + "=" * 78)
    ok = all((not polluted) and visible for _, polluted, visible in verdicts)
    print("结论：response 未被污染 且 告警仍可见 = %s" % (ok,))
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
