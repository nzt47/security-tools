"""TASK-S9-01 回归锚：对话编排「答非所问 + 跨轮串台」防线

现象（2026-09-13 真机实测，见 docs/zh/真用前置_模型凭证核查_20260913.md §八 D2）：

1. **跨轮串台**：连续两次「完全不同」的提问，``tool_steps`` / ``reasoning`` 与上一轮
   **逐字相同** ⇒ 取自全局单例实例属性（last-write-wins），未按会话隔离；
2. **答非所问**：问「2 加 3 等于多少？只回答数字」返回一整篇 ``# self_reflection``
   **技能文档正文**，而不是答案 ⇒ 语义层「低相关度误命中」短路把技能正文当作最终答案回吐。

本文件先固化现象（修复前必须**失败**），再长期作为回归防线守卫三件事：

A. **会话隔离**：最近一轮的 ``tool_steps`` / ``reasoning`` 必须按 ``session_id`` 隔离，
   本轮没有内容时返回**空**（``[]`` / ``None``），绝不复用其它会话或上一轮的值；
B. **答得对**：低相关度的语义层候选不得短路返回技能正文，必须降级 LLM；
   高相关度候选仍短路（既有公开契约不变）；
C. **禁止 ``or`` 回退旧值**：``x or self._last_x`` 这类写法一律不得再出现。

测试隔离说明（历史两次落盘污染教训）：
本文件用 ``__new__`` 绕过 LifecycleManager 重型初始化并 mock 掉记忆/Trace/埋点，
``autouse`` fixture 显式关闭 Trace 与学习度量，**不写**任何运行时台账/数据库。
"""

import os
import re
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.orchestrator.orchestrator import Orchestrator  # noqa: E402
from agent.orchestrator.orchestrator import _FALLBACK_MSG  # noqa: E402
from agent.guardrails.input_guard import GuardAction  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  真实观测数据（2026-09-13 真机复现，勿臆造）
# ═══════════════════════════════════════════════════════════════

#: 真机实测：SkillLoader.match("2 加 3 等于多少？只回答数字") 的 top1 形态
#: （retrieval_method=rrf，RRF 按 rank-1 归一化 ⇒ top1 分数恒 ≈1.0；
#:  tfidf 原始相似度仅 0.1，属噪声级）
LOW_RELEVANCE_HIT = {
    "skill_id": "self_reflection",
    "rrf_normalized_score": 0.9954,
    "breakdown": {
        "tfidf_rank": 2,
        "vector_rank": None,
        "bm25_rank": 1,
        "tfidf_score": 0.1,
        "vector_score": None,
        "bm25_score": 3.5184,
        "rrf_score": 0.016318,
        "rrf_normalized": 0.9954,
    },
}

#: 真机实测：SkillLoader.match("自我反思一下你的回答") 的 top1 形态（真实命中）
GENUINE_HIT = {
    "skill_id": "self_reflection",
    "rrf_normalized_score": 1.0,
    "breakdown": {
        "tfidf_rank": 1,
        "vector_rank": None,
        "bm25_rank": 1,
        "tfidf_score": 0.4444,
        "vector_score": None,
        "bm25_score": 15.1856,
        "rrf_score": 0.032,
        "rrf_normalized": 1.0,
    },
}

_REPO_SKILL_MD = (Path(project_root) / "data" / "skills_repo"
                  / "self_reflection" / "skill.md")


def _real_skill_doc_body() -> str:
    """读取真实技能文档正文（去掉 YAML front matter），用于「不得回吐技能正文」断言。"""
    text = _REPO_SKILL_MD.read_text(encoding="utf-8")
    # 去掉 --- ... --- front matter，保留正文
    parts = text.split("---", 2)
    body = parts[2] if len(parts) >= 3 else text
    return body.strip()


# ═══════════════════════════════════════════════════════════════
#  Mock 工厂
# ═══════════════════════════════════════════════════════════════

def _mock_workflow_result(matched=False, output="", intent="", confidence=0.0):
    m = MagicMock()
    m.matched = matched
    m.output = output
    m.intent = intent
    m.confidence = confidence
    m.execution_time_ms = 0.0
    m.rule_name = ""
    m.data = None
    return m


def _mock_match_result(matches, retrieval_method="rrf"):
    m = MagicMock()
    m.matches = matches
    m.retrieval_method = retrieval_method
    m.reranked = False
    m.fallback_used = False
    m.elapsed_ms = 1.0
    m.total_scanned = len(matches)
    m.estimated_total_tokens = 100
    return m


def _mock_skill_match(skill_id, score, breakdown=None):
    """构造 SkillMatch 替身

    ``breakdown=None``（默认）→ 不设置 ``score_breakdown``（MagicMock 自动属性，
    非 dict）⇒ 相关度门控按「信号不可用」跳过，保持对未改造调用方的向后兼容。
    """
    m = MagicMock()
    m.skill_id = skill_id
    m.score = score
    m.name = skill_id
    m.description = ""
    m.category = "custom"
    m.tags = []
    m.version = "0.1.0"
    m.enabled = True
    m.estimated_tokens = 100
    if breakdown is not None:
        m.score_breakdown = dict(breakdown)
    return m


def _mock_skills_service(matches, instruction):
    svc = MagicMock()
    svc.loader = MagicMock()
    svc.loader.match = MagicMock(return_value=_mock_match_result(matches))
    svc.loader.load_instruction = MagicMock(
        return_value={"skill_id": "x", "instruction": instruction,
                      "estimated_tokens": 100, "layer": 2})
    return svc


def _make_orchestrator():
    """最小化 Orchestrator（``__new__`` 绕过 LifecycleManager 重型初始化）"""
    orch = Orchestrator.__new__(Orchestrator)

    orch._running = True
    orch._interaction_count = 0
    orch._interaction_lock = threading.Lock()
    orch._session_id = "test_session"
    orch._last_was_template = False
    orch._last_context_warning = None
    orch._current_tool_steps = []
    orch._semantic_matched_skills = []
    orch._memory_token_limit = 8000
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
    orch._memory.get_context = MagicMock(return_value=[])
    orch._memory.load_summary = MagicMock(return_value=(None, None))
    orch._memory.get_working_memory = MagicMock(return_value={})
    orch._memory.get_budget_context = MagicMock(return_value=[])
    orch._memory._token_counter = MagicMock()
    orch._memory._token_counter.count = MagicMock(return_value=100)
    orch._memory._token_counter.count_messages = MagicMock(return_value=100)
    orch._memory._storage = MagicMock()
    orch._memory._storage.load_recent_messages = MagicMock(return_value=[])
    orch._memory.compress_rounds = 0

    orch._workflow_engine = MagicMock()
    orch._workflow_engine.try_match = MagicMock(
        return_value=_mock_workflow_result(matched=False))

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

    orch._check_context_usage = MagicMock(return_value=None)
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
    orch._build_reject_response = MagicMock(return_value="REJECT_RESPONSE")

    return orch


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch):
    """显式隔离 Trace / 埋点：本套件不得写任何运行时台账或数据库。"""
    import agent.orchestrator.orchestrator as _mod

    monkeypatch.setattr(_mod, "_MONITORING_AVAILABLE", False, raising=False)
    monkeypatch.setattr(_mod, "trace_store", MagicMock(), raising=False)
    monkeypatch.setattr(_mod, "_emit_learning_metric",
                        lambda *a, **kw: None, raising=False)
    # 语义层配置走 classmethod 覆写，避免触碰 semantic_config SQLite
    monkeypatch.setattr(
        Orchestrator, "_load_semantic_layer_config",
        classmethod(lambda cls: {
            "enabled": True, "min_score": 0.3, "top_k": 5,
            "use_vector": True, "use_bm25": True,
            "use_reranker": False, "fusion_mode": "rrf",
        }))
    yield


def _process_ctx(mock_svc):
    """process() 中会外呼的依赖统一 patch（沿用三层路由 e2e 的隔离基线）"""
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


def _ConfidenceLow():
    from agent.response_workflows import Confidence
    return Confidence.LOW


def _run_process(orch, text, session_id):
    """跑一轮 process()，返回 (result, llm_calls)"""
    llm_calls = []
    orch._call_llm = MagicMock(
        side_effect=lambda *a, **kw: llm_calls.append(1) or "LLM_ANSWER")
    with _process_ctx(orch._mock_skills_service):
        result = orch.process(text, session_id=session_id)
    return result, llm_calls


# ═══════════════════════════════════════════════════════════════
#  A0. TurnStateStore 直接单测（新模块，独立契约）
# ═══════════════════════════════════════════════════════════════

class TestTurnStateStore单元:
    """新模块 agent/orchestrator/turn_state.py 的独立契约测试"""

    def _store(self):
        from agent.orchestrator.turn_state import TurnStateStore
        return TurnStateStore(max_sessions=3)

    def test_begin把current滚入previous并清空current(self):
        st = self._store()
        st.set("s", tool_steps=[{"tool": "a"}], reasoning="思考")
        st.begin("s")
        assert st.snapshot("s") == {"tool_steps": [], "reasoning": None}
        assert st.previous("s") == {"tool_steps": [{"tool": "a"}], "reasoning": "思考"}

    def test_set显式None真实落None_不做or回退(self):
        st = self._store()
        st.set("s", reasoning="旧值")
        st.set("s", reasoning=None)
        assert st.snapshot("s")["reasoning"] is None, (
            "显式 None 必须真实落 None（这正是 D2 串台的注入点）")

    def test_set未传字段保持不变(self):
        st = self._store()
        st.set("s", tool_steps=[{"tool": "a"}], reasoning="r")
        st.set("s", reasoning="r2")
        snap = st.snapshot("s")
        assert snap["tool_steps"] == [{"tool": "a"}]
        assert snap["reasoning"] == "r2"

    def test_快照是副本_外部修改不污染内部(self):
        st = self._store()
        steps = [{"tool": "a"}]
        st.set("s", tool_steps=steps)
        snap = st.snapshot("s")
        snap["tool_steps"].append({"tool": "b"})
        steps.append({"tool": "c"})
        assert st.snapshot("s")["tool_steps"] == [{"tool": "a"}]

    def test_未知会话返回空(self):
        st = self._store()
        assert st.snapshot("nope") == {"tool_steps": [], "reasoning": None}
        assert st.previous("nope") == {"tool_steps": [], "reasoning": None}

    def test_sessions_clear_len_iter(self):
        st = self._store()
        st.set("a", reasoning="1")
        st.set("b", reasoning="2")
        assert sorted(st.sessions()) == ["a", "b"]
        assert len(st) == 2
        assert sorted(list(st)) == ["a", "b"]
        st.clear()
        assert len(st) == 0 and st.sessions() == []

    def test_会话上限淘汰最旧(self):
        st = self._store()  # max_sessions=3
        for i in range(5):
            st.set("s%d" % i, reasoning="r%d" % i)
        keys = st.sessions()
        assert len(keys) == 3
        assert "s0" not in keys and "s1" not in keys
        assert keys[-1] == "s4"

    def test_会话键归一化(self):
        from agent.orchestrator.turn_state import (
            normalize_session_key, DEFAULT_SESSION_KEY)
        assert normalize_session_key(None) == DEFAULT_SESSION_KEY
        assert normalize_session_key("") == DEFAULT_SESSION_KEY
        assert normalize_session_key("   ") == DEFAULT_SESSION_KEY
        assert normalize_session_key(" sess_x ") == "sess_x"


# ═══════════════════════════════════════════════════════════════
#  A. 跨轮串台：按会话隔离
# ═══════════════════════════════════════════════════════════════

class Test跨轮串台会话隔离:
    """验收判据 1 + 3：tool_steps / reasoning 按 session 隔离，本轮无内容返回空"""

    def test_两个会话交替_各自状态互不污染(self):
        """验收判据 3：A/B 两会话交替写入，互不污染"""
        orch = _make_orchestrator()
        orch._set_turn_state("sess_A", tool_steps=[{"tool": "list_directory"}],
                             reasoning="A 的思考")
        orch._set_turn_state("sess_B", tool_steps=[{"tool": "read_file"}],
                             reasoning="B 的思考")

        a = orch.last_turn_state("sess_A")
        b = orch.last_turn_state("sess_B")
        assert a["tool_steps"] == [{"tool": "list_directory"}]
        assert a["reasoning"] == "A 的思考"
        assert b["tool_steps"] == [{"tool": "read_file"}]
        assert b["reasoning"] == "B 的思考"
        assert a != b, "两个会话的 turn_state 不得相等（串台判据）"

    def test_本轮未写入_返回空而非其它会话或上一轮的值(self):
        """验收判据 1：本轮无内容 ⇒ 返回空（[] / None），绝不复用上一轮。"""
        orch = _make_orchestrator()
        orch._set_turn_state("sess_A", tool_steps=[{"tool": "list_directory"}],
                             reasoning="上一轮的思考")

        # 另一会话从未写入过
        fresh = orch.last_turn_state("sess_B")
        assert fresh["tool_steps"] == []
        assert fresh["reasoning"] is None

    def test_每轮入口清空_同一会话第二轮无内容也不得沿用第一轮(self):
        """验收判据 1（同一会话）：每轮入口先清空，本轮没写就返回空。"""
        orch = _make_orchestrator()
        orch._set_turn_state("sess_A", tool_steps=[{"tool": "list_directory"}],
                             reasoning="第一轮的思考")

        orch._begin_turn("sess_A")
        state = orch.last_turn_state("sess_A")
        assert state["tool_steps"] == [], "本轮入口清空后不得残留上一轮 tool_steps"
        assert state["reasoning"] is None, "本轮入口清空后不得残留上一轮 reasoning"

    def test_v2路径_reasoning为None时不得回退上一轮的reasoning(self, monkeypatch):
        """★ 串台根因锚：``_result.get("reasoning") or self._last_reasoning``

        D2 现象「reasoning 与上一轮逐字相同」的直接注入点：
        本轮 ``reasoning`` 为 None 时被 ``or`` 回退成上一轮的值。
        修复后必须是**显式赋值**：本轮没有 → None。
        """
        orch = _make_orchestrator()

        # 第一轮（会话 A）：有 reasoning
        orch._call_llm_v2 = Orchestrator._call_llm_v2.__get__(orch, Orchestrator)
        monkeypatch.setattr(
            "agent.orchestrator.orchestrator._get_template",
            lambda: ("{current_date}{body_status}{mode_name}{mode_description}"
                     "{memory_context}{tool_status}{skill_instructions}"))
        monkeypatch.setattr(
            "agent.tools.get_tool_defs", lambda **kw: [], raising=False)

        orch._tool_calling_service = MagicMock()
        orch._tool_calling_service.chat_with_steps = MagicMock(
            return_value={"text": "第一轮回答", "steps": [{"tool": "list_directory"}],
                          "reasoning": "第一轮的思考"})
        orch._llm = MagicMock()
        orch._llm.model = "main-model"
        orch._run_llm_bounded = MagicMock(side_effect=lambda fn: fn())

        r1 = orch._call_llm_v2("帮我列出当前工作目录下的文件", "",
                               session_id="sess_A")
        assert r1 == "第一轮回答"
        assert orch.last_turn_state("sess_A")["reasoning"] == "第一轮的思考"
        # 真实写路径不得再创建全局实例属性（串台载体必须下线）
        assert not hasattr(orch, "_last_tool_steps"), (
            "全局 _last_tool_steps 已下线，写路径不得再创建它")
        assert not hasattr(orch, "_last_reasoning"), (
            "全局 _last_reasoning 已下线，写路径不得再创建它")

        # 第二轮（会话 B）：本轮 LLM 没有返回 reasoning
        orch._tool_calling_service.chat_with_steps = MagicMock(
            return_value={"text": "5", "steps": [], "reasoning": None})
        r2 = orch._call_llm_v2("2 加 3 等于多少？只回答数字", "",
                               session_id="sess_B")
        assert r2 == "5"

        state_b = orch.last_turn_state("sess_B")
        assert state_b["reasoning"] is None, (
            "本轮 reasoning 为 None 时必须显式落 None，不得 or 回退上一轮的值")
        assert state_b["tool_steps"] == [], "B 会话本轮无工具，不得拿到 A 的步骤"

        # 会话 A 的历史不被 B 的写入污染
        assert orch.last_turn_state("sess_A")["reasoning"] == "第一轮的思考"

    def test_会话数上限_有界不无限增长(self):
        """会话级字典必须有界（长跑进程不得因会话无限增长而泄漏内存）"""
        orch = _make_orchestrator()
        cap = orch._TURN_STATE_MAX_SESSIONS
        for i in range(cap + 50):
            orch._set_turn_state("sess_%d" % i, tool_steps=[], reasoning="r%d" % i)
        assert len(orch._turn_state_sessions()) <= cap


# ═══════════════════════════════════════════════════════════════
#  B. 答非所问：语义层低相关度误命中不得短路回吐技能正文
# ═══════════════════════════════════════════════════════════════

class Test答非所问_语义层误命中:
    """验收判据 2：普通问题必须由 LLM 作答，不得把技能文档当作答案"""

    def test_低相关度命中_不得短路返回技能文档_必须降级LLM(self):
        """★ 答非所问根因锚（真机分数形态：rrf_normalized=0.9954 / tfidf_score=0.1）

        修复前：语义层短路 ⇒ response = 技能正文，LLM 未被调用。
        修复后：相关度门控拦截 ⇒ 降级 LLM 作答。
        """
        orch = _make_orchestrator()
        skill_doc = _real_skill_doc_body()
        orch._mock_skills_service = _mock_skills_service(
            matches=[_mock_skill_match("self_reflection",
                                       LOW_RELEVANCE_HIT["rrf_normalized_score"],
                                       LOW_RELEVANCE_HIT["breakdown"])],
            instruction=skill_doc)

        llm_calls = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_calls.append(1) or "5")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("2 加 3 等于多少？只回答数字",
                                  session_id="sess_math")

        text = result.get("data") or result.get("response") or ""
        assert "5" in text, "算术提问必须由 LLM 作答（含 5），实际=%r" % (text[:120],)
        assert llm_calls == [1], "低相关度命中不得短路，必须调用 LLM"
        assert "# self_reflection" not in text, "不得把技能文档正文当作答案回吐"
        assert "适用场景" not in text, "不得把技能文档正文当作答案回吐"

    def test_高相关度命中_仍短路返回instruction_契约不变(self):
        """验收判据 5：真实高相关度命中仍短路返回 instruction（公开契约不变）"""
        orch = _make_orchestrator()
        orch._mock_skills_service = _mock_skills_service(
            matches=[_mock_skill_match("self_reflection",
                                       GENUINE_HIT["rrf_normalized_score"],
                                       GENUINE_HIT["breakdown"])],
            instruction="自我反思技能使用说明：回放关键步骤查找逻辑漏洞")

        llm_calls = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_calls.append(1) or "LLM")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("自我反思一下你的回答", session_id="sess_refl")

        assert result["success"] is True
        assert result["msg"] == "handled_by_semantic_layer"
        assert "自我反思技能使用说明" in result["data"]
        assert llm_calls == [], "高相关度命中仍应短路，不调用 LLM"

    def test_无score_breakdown时_保持既有行为_向后兼容(self):
        """相关度信号不可用时（未改造调用方 / 自定义 loader）保持既有阈值行为"""
        orch = _make_orchestrator()
        orch._mock_skills_service = _mock_skills_service(
            matches=[_mock_skill_match("skill_pdf", 0.85)],  # 无 score_breakdown
            instruction="PDF解析技能使用说明")
        result, llm_calls = _run_process(orch, "帮我解析PDF文件", "sess_pdf")
        assert result["msg"] == "handled_by_semantic_layer"
        assert llm_calls == []

    def test_仅BM25命中_融合分为排名归一化值_不得短路(self):
        """★ 真机形态②：``{tfidf_score: None, vector_score: None, bm25_score: 3.5184}``

        RRF 只融合 BM25（无界原始分）时，`rrf_normalized` = 1.0 是**排名归一化值**，
        不构成「相似度 ≥ min_score」的证据。修复前该形态直接把
        `# self_reflection` 技能正文返回给用户（答非所问）。
        """
        orch = _make_orchestrator()
        only_bm25 = {
            "tfidf_rank": None, "vector_rank": None, "bm25_rank": 1,
            "tfidf_score": None, "vector_score": None, "bm25_score": 3.5184,
            "rrf_score": 0.016393, "rrf_normalized": 1.0,
        }
        orch._mock_skills_service = _mock_skills_service(
            matches=[_mock_skill_match("self_reflection", 1.0, only_bm25)],
            instruction=_real_skill_doc_body())
        llm_calls = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_calls.append(1) or "5")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("2 加 3 等于多少？只回答数字", session_id="sess_bm25")
        text = result.get("data") or ""
        assert llm_calls == [1], "无可信有界相似度时必须降级 LLM"
        assert "5" in text
        assert "# self_reflection" not in text

    def test_相关度提取函数_只取有界相似度(self):
        """BM25 原始分无界（实测 3.5184），不得当作 [0,1] 相似度参与阈值比较"""
        match = _mock_skill_match("self_reflection", 0.9954,
                                  LOW_RELEVANCE_HIT["breakdown"])
        assert Orchestrator._bounded_relevance(match) == pytest.approx(0.1)

        match2 = _mock_skill_match("self_reflection", 1.0, GENUINE_HIT["breakdown"])
        assert Orchestrator._bounded_relevance(match2) == pytest.approx(0.4444)

        # 无 breakdown（非 dict）→ 信号不可用
        assert Orchestrator._bounded_relevance(_mock_skill_match("x", 0.9)) is None

        # 仅 vector 有界分时取其值
        m3 = _mock_skill_match("x", 1.0, {"tfidf_score": None, "vector_score": 0.7,
                                          "bm25_score": 9.9})
        assert Orchestrator._bounded_relevance(m3) == pytest.approx(0.7)


# ═══════════════════════════════════════════════════════════════
#  B2. 答非所问坏形态①：工作流层把「工具原始载荷」当作最终答案回吐
# ═══════════════════════════════════════════════════════════════

#: 真机实测：list_directory 的原始返回值（片段）——「帮我列出当前工作目录下的文件」
#: 的 response 曾是它的 Python repr（len=36074）
RAW_LIST_DIRECTORY = {
    "ok": True,
    "path": ".",
    "abs_path": "C:\\Users\\Administrator\\agent",
    "type": "dir",
    "items": [{"type": "dir", "size": 0, "name": "Modules"},
              {"type": "file", "size": 1234, "name": "app_server.py"}],
    "total": 195,
}


def _wf_hit_result(raw_output, tools=("list_directory",)):
    return {
        "output": str(raw_output or ""),
        "raw_output": raw_output,
        "step_tools": list(tools),
        "workflow_id": "wf-f19dc52c",
        "workflow_name": "自动学习: 列-出-当",
        "score": 0.93,
        "confidence": 0.75,
        "steps_executed": len(tools),
        "elapsed_ms": 12.0,
        "skipped_llm": True,
    }


class Test答非所问_工作流工具载荷:
    """TASK-S9-01 坏形态①：工具结果只作为素材，不得当作最终答案直接回吐"""

    def test_工作流输出为工具载荷_不得原样回吐_必须转LLM并记工具步骤(self):
        """真机分数/形态：wf-f19dc52c（list_directory path="."）输出是 dict 载荷

        修复前：``"output": str(result.output)`` ⇒ response = 载荷的 Python repr（36074 字符）。
        修复后：不短路回吐 ⇒ 载荷作为**素材**交给 LLM 转述，并把真实执行的工具记入 tool_steps。
        """
        orch = _make_orchestrator()
        orch._workflow_learning_layer_match = MagicMock(
            return_value=_wf_hit_result(RAW_LIST_DIRECTORY))
        orch._mock_skills_service = _mock_skills_service(matches=[], instruction="")

        llm_calls = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_calls.append(kw) or
            "当前工作目录下共有 195 项，包括 Modules 目录与 app_server.py 等文件。")

        with _process_ctx(orch._mock_skills_service):
            result = orch.process("帮我列出当前工作目录下的文件",
                                  session_id="sess_ls")

        text = result.get("data") or result.get("response") or ""
        # ① 不得把工具原始载荷当答案回吐
        assert "abs_path" not in text, "不得回吐工具原始载荷"
        assert "'ok': True" not in text, "不得回吐工具原始载荷的 repr"
        # ② 必须交给 LLM 转述成与提问相关的答案
        assert llm_calls, "工作流产出为工具载荷时必须下沉 LLM 生成答案"
        assert result["msg"] != "handled_by_workflow_learning"
        # ③ 素材与「本轮已执行工具」必须透传
        assert llm_calls[0].get("extra_material"), "必须把工具结果作为素材注入"
        assert llm_calls[0].get("allow_tools") is False, (
            "工作流已执行过工具，本轮不得再暴露工具（防重复副作用）")
        # ④ 判据 4：真实执行过的工具必须出现在本轮 tool_steps
        steps = orch.last_turn_state("sess_ls")["tool_steps"]
        assert any(s.get("tool") == "list_directory" for s in steps), steps

    def test_工作流输出为文本_仍短路返回_契约不变(self):
        """回归：工作流产出是用户可读文本时，保持既有 0-Token 短路契约"""
        orch = _make_orchestrator()
        orch._workflow_learning_layer_match = MagicMock(
            return_value=_wf_hit_result("已为你完成目录统计。", tools=("search_files",)))
        orch._mock_skills_service = _mock_skills_service(matches=[], instruction="")
        llm_calls = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_calls.append(1) or "LLM")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("统计项目里的文件", session_id="sess_wf_text")
        assert result["msg"] == "handled_by_workflow_learning"
        assert result["data"] == "已为你完成目录统计。"
        assert llm_calls == [], "文本产出仍应短路，不调用 LLM"

    def test_工具载荷判定函数(self):
        assert Orchestrator._bounded_relevance is not None  # 类可加载
        from agent.orchestrator.orchestrator import _looks_like_tool_payload
        assert _looks_like_tool_payload(RAW_LIST_DIRECTORY) is True
        assert _looks_like_tool_payload(repr(RAW_LIST_DIRECTORY)) is True
        assert _looks_like_tool_payload('{"ok": true, "total": 3}') is True
        assert _looks_like_tool_payload("[1, 2, 3]") is True
        assert _looks_like_tool_payload("当前目录下共有 195 项。") is False
        assert _looks_like_tool_payload("") is False
        assert _looks_like_tool_payload(None) is False


# ═══════════════════════════════════════════════════════════════
#  B3. 答非所问坏形态③：合法短答被「低置信度兜底」替换
# ═══════════════════════════════════════════════════════════════

class Test答非所问_短答不得被兜底替换:
    """验收判据 2：问「2 加 3 等于多少？只回答数字」→ 回答含 5

    修复前 ``_judge_llm_confidence`` 用 ``len(strip) < 5`` 判低置信度，
    LLM 的合法短答 "5" 会被替换成兜底文案（把已答对的答案丢掉）。
    """

    def test_算术题短答_不得被低置信度兜底替换(self):
        orch = _make_orchestrator()
        orch._workflow_learning_layer_match = MagicMock(return_value=None)
        orch._mock_skills_service = _mock_skills_service(matches=[], instruction="")
        llm_calls = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_calls.append(1) or "5")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("2 加 3 等于多少？只回答数字",
                                  session_id="sess_math_short")
        text = result.get("data") or result.get("response") or ""
        assert llm_calls == [1]
        assert "5" in text, "合法短答必须原样返回，实际=%r" % (text[:120],)
        assert _FALLBACK_MSG not in text, "不得用低置信度兜底文案替换正确答案"

    def test_空响应_仍触发低置信度兜底(self):
        """回归：空响应仍必须走兜底（语义未被收紧到失效）"""
        orch = _make_orchestrator()
        orch._workflow_learning_layer_match = MagicMock(return_value=None)
        orch._mock_skills_service = _mock_skills_service(matches=[], instruction="")
        orch._call_llm = MagicMock(return_value="")
        with _process_ctx(orch._mock_skills_service):
            result = orch.process("随便问点什么内容", session_id="sess_empty")
        assert _FALLBACK_MSG in (result.get("data") or "")


# ═══════════════════════════════════════════════════════════════
#  C. 禁止 `or` 回退旧值
# ═══════════════════════════════════════════════════════════════
class Test无or回退旧状态:
    """硬约束：``x or self._last_x`` 这类保留旧值的写法一律不得再出现"""

    _SRC = (Path(project_root) / "agent" / "orchestrator" / "orchestrator.py")

    @classmethod
    def _code_only(cls) -> str:
        """只扫描**代码**：剔除整行注释（修复说明里会引用历史写法，不应命中）"""
        lines = []
        for line in cls._SRC.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            lines.append(line)
        return "\n".join(lines)

    def test_orchestrator源码不得出现_or_回退上一轮状态(self):
        bad = re.findall(r"or\s+self\._last_(?:tool_steps|reasoning)\b",
                         self._code_only())
        assert bad == [], (
            "发现 `or` 回退旧状态的写法（跨轮串台注入点）: %r" % (bad,))

    def test_orchestrator源码不得残留全局_last属性读写(self):
        hits = re.findall(r"self\._last_(?:tool_steps|reasoning)\b",
                          self._code_only())
        assert hits == [], (
            "orchestrator.py 不得再读写全局 _last_tool_steps/_last_reasoning: %r"
            % (hits,))
