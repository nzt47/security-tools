"""[TLM] Orchestrator 三层路由端到端集成测试

验证 orchestrator.process() 的「规则层(WorkflowEngine) → 语义层(SkillLoader.match)
→ LLM」三层漏斗架构，以及拒识与降级路径。

测试边界:
- 用 unittest.mock 替换 WorkflowEngine / SkillLoader / LLM / 记忆 / 护栏等重型依赖
- 用 Orchestrator.__new__(Orchestrator) 绕过 LifecycleManager 重型初始化
- 聚焦三层路由分支决策与降级链路，不验证 LLM/向量模型真实推理

覆盖场景:
1. 规则层命中 → 短路返回 workflow_result，不调用语义层/LLM
2. 语义层命中 → 短路返回 instruction，不调用 LLM
3. 语义层未命中 → 降级 LLM
4. 语义层异常 → 降级 LLM（不抛错）
5. 语义层关闭(enabled=false) → 直接降级 LLM
6. 输入过短且三层未命中 → 拒识
7. 配置优先级：env 覆盖 config.yaml
"""

import os
import sys
import threading
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# 确保项目根目录在 sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.orchestrator.orchestrator import Orchestrator
from agent.orchestrator.response_builder import ResponseBuilder
from agent.guardrails.input_guard import GuardAction


# ═══════════════════════════════════════════════════════════════
# Mock 工厂
# ═══════════════════════════════════════════════════════════════

def _make_mock_workflow_result(matched=False, output="", intent="",
                                confidence=0.0, execution_time_ms=0.0):
    """构造 WorkflowEngine.try_match 返回值（WorkflowResult 替身）"""
    m = MagicMock()
    m.matched = matched
    m.output = output
    m.intent = intent
    m.confidence = confidence
    m.execution_time_ms = execution_time_ms
    m.rule_name = ""
    m.data = None
    return m


def _make_mock_skill_match(skill_id="skill_pdf", score=0.85,
                            name="PDF解析", description="解析PDF文件"):
    """构造 SkillMatch 替身"""
    m = MagicMock()
    m.skill_id = skill_id
    m.score = score
    m.name = name
    m.description = description
    m.estimated_tokens = 100
    m.category = "tool"
    m.tags = []
    m.version = "1.0.0"
    m.enabled = True
    return m


def _make_mock_match_result(matches=None, retrieval_method="rrf",
                              reranked=False, fallback_used=False):
    """构造 MatchResult 替身"""
    m = MagicMock()
    m.matches = matches or []
    m.retrieval_method = retrieval_method
    m.reranked = reranked
    m.fallback_used = fallback_used
    m.elapsed_ms = 12.5
    m.total_scanned = 10
    m.estimated_total_tokens = 500
    return m


def _make_mock_orchestrator():
    """构造最小化 Orchestrator 实例（绕过 LifecycleManager 重型初始化）

    用 __new__ 跳过 __init__，手动注入所有 process() 依赖的 mock 属性。
    """
    orch = Orchestrator.__new__(Orchestrator)

    # 基础状态
    orch._running = True
    orch._interaction_count = 0
    # 【不易】process() L334 `with self._interaction_lock:` 依赖此属性；生产代码
    # 由宿主 LifecycleManager/V2 optimized_init 创建，本工厂用 __new__ 绕过
    # __init__ 必须手动补齐，否则直接实例化时 process() 抛 AttributeError。
    orch._interaction_lock = threading.Lock()
    orch._session_id = "test_session"
    orch._last_was_template = False
    orch._last_context_warning = None
    orch._last_reasoning = None
    orch._last_tool_steps = []
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

    # V2 系统全部关闭（走标准路径，避免 Persona/LifeTrace 依赖）
    orch._v2_lifetrace = None
    orch._v2_persona = None
    orch._v2_distillation = None
    orch._trace_recorder = None
    orch._persona_extractor = None
    orch._persona_injector = None
    orch._injector = None

    # 依赖组件 mock
    orch._memory = MagicMock()
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
        return_value=_make_mock_workflow_result(matched=False))

    orch._behavior = MagicMock()
    orch._behavior.can_execute = MagicMock(return_value=(True, ""))
    orch._behavior.profile = MagicMock()
    orch._behavior.profile.label = "default"
    orch._behavior.profile.description = "test"
    orch._behavior.profile.enable_reflection = False
    orch._behavior.profile.response_prefix = ""
    orch._behavior.evaluate = MagicMock(return_value=MagicMock(value="normal"))

    orch._llm = None  # None → _call_llm 走 _build_offline_response
    orch._current_mode = MagicMock()
    orch._current_mode.value = "normal"

    # 输入/输出护栏
    orch._guardrails_input_guard = MagicMock()
    orch._guardrails_input_guard.check = MagicMock(
        return_value=MagicMock(action=GuardAction.ALLOW, reason="",
                                matched_pattern=""))
    orch._guardrails_output_guard = MagicMock()
    orch._guardrails_output_guard.check = MagicMock(
        return_value=MagicMock(modified=False, redacted_fields=[],
                                filtered=""))

    # Orchestrator 内部方法 mock（patch.object 在 fixture 中应用）
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
    # 【不易】默认绕过语义拒识（_should_reject）——本套件聚焦三层路由分支与
    # 降级链路，语义拒识行为由 test_orchestrator_reject.py 独立守卫。长度拒识
    # （_len_reject，不依赖 _should_reject）仍生效，test_输入过短且三层未命中_拒识 不受影响。
    orch._should_reject = MagicMock(return_value=(False, "test: bypass reject"))
    orch._run_persona_distillation = MagicMock()
    orch._guard_llm_output = MagicMock(side_effect=lambda resp, *a, **kw: resp)
    # 【不易】拒识兜底：默认放行，避免双未命中时 _should_reject 拦截 LLM 降级路径
    # （3 个降级 LLM 场景测试依赖此 mock：语义层未命中/异常/关闭）
    orch._should_reject = MagicMock(return_value=(False, "test_allow"))

    return orch


def _make_mock_skills_mgmt_service(matches=None, instruction="",
                                      match_exc=None, instr_exc=None):
    """构造 mock skills_mgmt service（含 loader.match / loader.load_instruction）

    Args:
        matches: loader.match 返回的 matches 列表（None 表示返回空 matches）
        instruction: load_instruction 返回的 instruction 文本
        match_exc: loader.match 抛出的异常（None 不抛）
        instr_exc: load_instruction 抛出的异常（None 不抛）
    """
    svc = MagicMock()
    svc.loader = MagicMock()

    if match_exc is not None:
        svc.loader.match = MagicMock(side_effect=match_exc)
    else:
        result = _make_mock_match_result(matches=matches or [])
        svc.loader.match = MagicMock(return_value=result)

    if instr_exc is not None:
        svc.loader.load_instruction = MagicMock(side_effect=instr_exc)
    else:
        svc.loader.load_instruction = MagicMock(
            return_value={"skill_id": "x", "instruction": instruction,
                          "estimated_tokens": 100, "layer": 2})

    return svc


@pytest.fixture
def orch():
    """默认 mock orchestrator：规则层未命中，便于测试后续层"""
    return _make_mock_orchestrator()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """隔离环境变量，避免本地 .env 污染测试

    清除语义层相关 env + SQLite 持久化状态，让测试通过 mock 控制
    """
    for key in ("ORCHESTRATOR_SEMANTIC_LAYER_ENABLED",
                "ORCHESTRATOR_SEMANTIC_MIN_SCORE",
                "ORCHESTRATOR_REJECT_MIN_LENGTH"):
        monkeypatch.delenv(key, raising=False)
    # 清除语义层状态（内存 + SQLite 持久化），避免跨测试污染
    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._SEM_DB_LOADED = False
    Orchestrator._clear_semantic_config_cache()
    # 清除 SQLite 持久化数据（避免上一个测试的热更值残留）
    try:
        conn = Orchestrator._get_semantic_db_conn()
        conn.execute("DELETE FROM semantic_config_overrides")
        conn.commit()
    except Exception:
        pass  # db 不存在或不可用时跳过
    yield


# ═══════════════════════════════════════════════════════════════
# 端到端测试：三层路由分支
# ═══════════════════════════════════════════════════════════════

class TestOrchestrator三层路由E2E:
    """端到端验证 process() 的规则→语义→LLM 三层路由决策"""

    # ── L1 规则层路径 ──

    def test_规则层命中_短路返回workflow(self, orch):
        """场景1: WorkflowEngine 命中 → 返回 workflow_result，不调用语义层/LLM"""
        # 配置：规则层命中
        orch._workflow_engine.try_match.return_value = _make_mock_workflow_result(
            matched=True, output="规则层回复", intent="greeting",
            confidence=0.95, execution_time_ms=1.5)

        call_llm_called = []
        def _llm_side_effect(*a, **kw):
            call_llm_called.append(True)
            return "LLM_RESPONSE"
        orch._call_llm = MagicMock(side_effect=_llm_side_effect)

        semantic_called = []
        def _sem_side_effect(*a, **kw):
            semantic_called.append(True)
            return None
        orch._semantic_layer_match = MagicMock(side_effect=_sem_side_effect)

        result = orch.process("你好")

        assert result["success"] is True
        assert result["msg"] == "handled_by_workflow"
        assert result["data"]["output"] == "规则层回复"
        assert result["data"]["intent"] == "greeting"
        # 规则层命中后不应调用语义层和 LLM
        assert semantic_called == []
        assert call_llm_called == []

    # ── L2 语义层路径 ──

    def test_语义层命中_短路返回instruction(self, orch, monkeypatch):
        """场景2: 规则层未命中 + 语义层命中 → 返回 instruction，不调用 LLM"""
        # 规则层未命中（默认）
        # mock 语义层配置：启用
        sem_cfg = {"enabled": True, "min_score": 0.3, "top_k": 5,
                   "use_vector": True, "use_bm25": True,
                   "use_reranker": False, "fusion_mode": "rrf"}
        monkeypatch.setattr(Orchestrator, "_load_semantic_layer_config",
                            classmethod(lambda cls: dict(sem_cfg)))

        # mock skills_mgmt service 返回高分匹配 + 非空 instruction
        mock_svc = _make_mock_skills_mgmt_service(
            matches=[_make_mock_skill_match(skill_id="skill_pdf", score=0.85)],
            instruction="PDF解析技能使用说明：调用 fitz.open() 打开文件...")

        llm_called = []
        orch._call_llm = MagicMock(side_effect=lambda *a, **kw: llm_called.append(1) or "LLM")

        with patch("agent.state_manager.get_skills_mgmt_service",
                   return_value=mock_svc), \
             patch("agent.response_workflows.IntentRouter.classify",
                   return_value=("unknown", _ConfidenceLow())), \
             patch("agent.response_workflows.ResponseTemplates.for_intent",
                   return_value=None), \
             patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
                   return_value=[]), \
             patch("agent.orchestrator.dialog_state.get_dialog_state",
                   return_value=MagicMock(last_keywords=None, resolve=MagicMock(return_value=None))):
            result = orch.process("帮我解析PDF文件")

        assert result["success"] is True
        assert result["msg"] == "handled_by_semantic_layer"
        assert "PDF解析技能使用说明" in result["data"]
        # 语义层命中后不应调用 LLM
        assert llm_called == []

    def test_模板层命中_带trace_真正短路返回(self, orch, monkeypatch):
        """场景8(回归): 模板命中 + trace 启用（get_trace_id 非 None）→ 必须真正短路

        回归 2026-08-03 bug: 模板命中分支构造 TraceSpan 时缺必填字段 start_time，
        抛 TypeError 被 except Exception 吞掉，导致模板命中后继续下沉 wfl→semantic→LLM，
        产生 2 条 route_decision 且白耗 Token。修复后模板命中应恰好 1 条决策且不调 LLM/语义层。
        """
        monkeypatch.setattr("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", True)
        monkeypatch.setattr("agent.orchestrator.orchestrator.get_trace_id",
                            lambda: "trace_tpl_001")
        monkeypatch.setattr("agent.orchestrator.orchestrator.trace_store", MagicMock())
        monkeypatch.setattr("agent.orchestrator.orchestrator.get_metrics_collector",
                            lambda: MagicMock())

        llm_called = []
        orch._call_llm = MagicMock(side_effect=lambda *a, **kw: llm_called.append(1) or "LLM")
        sem_called = []
        orch._semantic_layer_match = MagicMock(
            side_effect=lambda *a, **kw: sem_called.append(1) or None)

        from agent.orchestrator.routing_observability import RouteContext
        RouteContext._var.set(None)

        with patch("agent.response_workflows.IntentRouter.classify",
                   return_value=("schedule", MagicMock(name="HIGH", value=1))), \
             patch("agent.response_workflows.ResponseTemplates.for_intent",
                   return_value="模板回复"), \
             patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
                   return_value=[]), \
             patch("agent.orchestrator.dialog_state.get_dialog_state",
                   return_value=MagicMock(last_keywords=None, resolve=MagicMock(return_value=None))):
            result = orch.process("帮我把明天的会议改到下午")

        assert result["success"] is True
        assert result["data"] == "模板回复"
        assert result["msg"] == "ok"
        # 模板命中必须真正短路：不调 LLM、不调语义层
        assert llm_called == []
        assert sem_called == []

    def test_语义层未命中_降级LLM(self, orch, monkeypatch):
        """场景3: 规则层未命中 + 语义层未命中(matches=[]) → 调用 LLM"""
        sem_cfg = {"enabled": True, "min_score": 0.3, "top_k": 5,
                   "use_vector": True, "use_bm25": True,
                   "use_reranker": False, "fusion_mode": "rrf"}
        monkeypatch.setattr(Orchestrator, "_load_semantic_layer_config",
                            classmethod(lambda cls: dict(sem_cfg)))

        # skills_mgmt 返回空 matches（未命中）
        mock_svc = _make_mock_skills_mgmt_service(matches=[], instruction="")

        llm_called = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_called.append(1) or "LLM_RESPONSE")

        with patch("agent.state_manager.get_skills_mgmt_service",
                   return_value=mock_svc), \
             patch("agent.response_workflows.IntentRouter.classify",
                   return_value=("unknown", _ConfidenceLow())), \
             patch("agent.response_workflows.ResponseTemplates.for_intent",
                   return_value=None), \
             patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
                   return_value=[]), \
             patch("agent.orchestrator.dialog_state.get_dialog_state",
                   return_value=MagicMock(last_keywords=None, resolve=MagicMock(return_value=None))):
            result = orch.process("帮我写一首关于春天的诗")

        assert result["success"] is True
        assert result["msg"] == "ok"  # LLM 路径返回 success(msg="ok")
        assert llm_called == [1]  # LLM 被调用

    def test_语义层异常_降级LLM不抛错(self, orch, monkeypatch):
        """场景4: SkillLoader.match 抛异常 → 降级 LLM，无异常向上传播"""
        sem_cfg = {"enabled": True, "min_score": 0.3, "top_k": 5,
                   "use_vector": True, "use_bm25": True,
                   "use_reranker": False, "fusion_mode": "rrf"}
        monkeypatch.setattr(Orchestrator, "_load_semantic_layer_config",
                            classmethod(lambda cls: dict(sem_cfg)))

        # skills_mgmt service 的 loader.match 抛异常
        mock_svc = _make_mock_skills_mgmt_service(
            match_exc=RuntimeError("模拟向量模型崩溃"))

        llm_called = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_called.append(1) or "LLM_RESPONSE")

        with patch("agent.state_manager.get_skills_mgmt_service",
                   return_value=mock_svc), \
             patch("agent.response_workflows.IntentRouter.classify",
                   return_value=("unknown", _ConfidenceLow())), \
             patch("agent.response_workflows.ResponseTemplates.for_intent",
                   return_value=None), \
             patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
                   return_value=[]), \
             patch("agent.orchestrator.dialog_state.get_dialog_state",
                   return_value=MagicMock(last_keywords=None, resolve=MagicMock(return_value=None))):
            # 不应抛异常
            result = orch.process("帮我分析这段代码")

        assert result["success"] is True
        assert llm_called == [1]  # 降级到 LLM

    # ── 配置驱动路径 ──

    def test_语义层关闭_直接降级LLM(self, orch, monkeypatch):
        """场景5: config enabled=false → 跳过语义层，调用 LLM"""
        sem_cfg = {"enabled": False, "min_score": 0.3, "top_k": 5,
                   "use_vector": True, "use_bm25": True,
                   "use_reranker": False, "fusion_mode": "rrf"}
        monkeypatch.setattr(Orchestrator, "_load_semantic_layer_config",
                            classmethod(lambda cls: dict(sem_cfg)))

        # 即便 skills_mgmt 返回高分匹配，enabled=false 也不应调用 loader.match
        mock_svc = _make_mock_skills_mgmt_service(
            matches=[_make_mock_skill_match(score=0.99)],
            instruction="不应被加载的说明")
        llm_called = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_called.append(1) or "LLM_RESPONSE")

        with patch("agent.state_manager.get_skills_mgmt_service",
                   return_value=mock_svc), \
             patch("agent.response_workflows.IntentRouter.classify",
                   return_value=("unknown", _ConfidenceLow())), \
             patch("agent.response_workflows.ResponseTemplates.for_intent",
                   return_value=None), \
             patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
                   return_value=[]), \
             patch("agent.orchestrator.dialog_state.get_dialog_state",
                   return_value=MagicMock(last_keywords=None, resolve=MagicMock(return_value=None))):
            result = orch.process("帮我解析PDF文件")

        assert result["success"] is True
        assert llm_called == [1]
        # enabled=false 时 loader.match 不应被调用
        mock_svc.loader.match.assert_not_called()

    def test_配置优先级_env覆盖config(self, orch, monkeypatch):
        """场景7: env ORCHESTRATOR_SEMANTIC_LAYER_ENABLED=false 覆盖 config.yaml enabled=true

        验证 _load_semantic_layer_config 的分层优先级：
        环境变量 > config.yaml > 硬编码默认值
        """
        # 先清除缓存，确保从 env 重新读取
        Orchestrator._clear_semantic_config_cache()

        # 设置 env：关闭语义层（覆盖 config.yaml 的 enabled: true）
        monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_LAYER_ENABLED", "false")

        # 读取配置：应受 env 覆盖，enabled=False
        cfg = Orchestrator._load_semantic_layer_config()
        assert cfg["enabled"] is False, "env ORCHESTRATOR_SEMANTIC_LAYER_ENABLED=false 应覆盖 config.yaml enabled=true"

        # 再设为 true 验证反向覆盖
        monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_LAYER_ENABLED", "true")
        Orchestrator._clear_semantic_config_cache()
        cfg2 = Orchestrator._load_semantic_layer_config()
        assert cfg2["enabled"] is True

        # 验证 min_score 也可被 env 覆盖
        monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MIN_SCORE", "0.555")
        Orchestrator._clear_semantic_config_cache()
        cfg3 = Orchestrator._load_semantic_layer_config()
        assert cfg3["min_score"] == 0.555

    # ── 拒识路径 ──

    def test_输入过短且三层未命中_拒识(self, orch, monkeypatch):
        """场景6: len<3 + 规则/语义未命中 → 返回拒识文案"""
        sem_cfg = {"enabled": True, "min_score": 0.3, "top_k": 5,
                   "use_vector": True, "use_bm25": True,
                   "use_reranker": False, "fusion_mode": "rrf"}
        monkeypatch.setattr(Orchestrator, "_load_semantic_layer_config",
                            classmethod(lambda cls: dict(sem_cfg)))

        mock_svc = _make_mock_skills_mgmt_service(matches=[], instruction="")
        llm_called = []
        orch._call_llm = MagicMock(
            side_effect=lambda *a, **kw: llm_called.append(1) or "LLM_RESPONSE")

        # 输入长度 < 3（默认 ORCHESTRATOR_REJECT_MIN_LENGTH=3）
        with patch("agent.state_manager.get_skills_mgmt_service",
                   return_value=mock_svc), \
             patch("agent.response_workflows.IntentRouter.classify",
                   return_value=("unknown", _ConfidenceLow())), \
             patch("agent.response_workflows.ResponseTemplates.for_intent",
                   return_value=None), \
             patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
                   return_value=False), \
             patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
                   return_value=[]), \
             patch("agent.orchestrator.dialog_state.get_dialog_state",
                   return_value=MagicMock(last_keywords=None, resolve=MagicMock(return_value=None))):
            result = orch.process("啊")  # 1 字符 < 3

        assert result["success"] is True
        assert "不理解" in result["data"] or "详细描述" in result["data"]
        assert llm_called == []  # 拒识后不应调用 LLM


# ═══════════════════════════════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════════════════════════════

class _ConfidenceLow:
    """Confidence.LOW 替身（避免导入 response_workflows 的真实枚举依赖）"""
    name = "LOW"
    value = 2

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return True

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return True

    def __eq__(self, other):
        return True  # 任意比较都视作满足（模板层 for_intent 内部判断）

    def __hash__(self):
        return hash("LOW")


# ═══════════════════════════════════════════════════════════════
# 语义层配置热更测试（方案3：HTTP API + 并发场景）
# ═══════════════════════════════════════════════════════════════

def test_API热更_参数校验与生效():
    """测试 HTTP API 热更接口的参数校验 + 热更生效

    覆盖: GET 查询 / POST 热更 / 非法参数拒绝 / 未知键忽略
    """
    from flask import Flask
    from unittest.mock import MagicMock
    from agent.server_routes.routes_config import register_routes

    # 构造最小 Flask app + mock state
    app = Flask(__name__)
    app.config["TESTING"] = True
    state = MagicMock()
    state.Yunshu = MagicMock()
    state.Yunshu.get_config = MagicMock(return_value={})
    state.Yunshu.configure_llm = MagicMock(return_value={"ok": True})
    state.session_mgr = MagicMock()
    state.session_mgr.get_current_id = MagicMock(return_value="test")
    state.session_mgr.create_session = MagicMock(return_value={"id": "test"})
    state.session_mgr.clear_messages = MagicMock()
    state.network_config_mgr = MagicMock()
    state.network_config_mgr.get_all = MagicMock(return_value={})
    state.network_config_mgr.get_raw_config = MagicMock(return_value={})
    state.network_config_mgr.get_llm_instances = MagicMock(return_value=[])
    state.network_config_mgr.get_mcp_services = MagicMock(return_value=[])
    state.network_config_mgr.get_change_log = MagicMock(return_value=[])
    state.network_config_mgr.get_search_engines = MagicMock(return_value=[])
    state.search_engine = MagicMock()
    state.chat_history = MagicMock()

    register_routes(app, state)
    client = app.test_client()

    try:
        # 1. GET 查询当前配置
        resp = client.get("/api/orchestrator/semantic-config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "config" in data
        assert "min_score" in data["config"]

        # 2. POST 热更 min_score
        resp = client.post("/api/orchestrator/semantic-config",
                           json={"min_score": 0.5})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["current_config"]["min_score"] == 0.5
        assert data["overrides"]["min_score"] == 0.5

        # 3. 非法 min_score（超范围）
        resp = client.post("/api/orchestrator/semantic-config",
                           json={"min_score": 1.5})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False
        assert "范围" in data["error"]

        # 4. 类型错误
        resp = client.post("/api/orchestrator/semantic-config",
                           json={"min_score": "abc"})
        assert resp.status_code == 400

        # 5. 未知键忽略
        resp = client.post("/api/orchestrator/semantic-config",
                           json={"unknown_key": 123, "min_score": 0.7})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["current_config"]["min_score"] == 0.7
        assert "unknown_key" not in data["overrides"]

        # 6. 空请求体
        resp = client.post("/api/orchestrator/semantic-config",
                           json={})
        assert resp.status_code == 400

        print("\n" + "=" * 60)
        print(" API 热更接口测试: 全部通过 (6 场景)")
        print("=" * 60)
        print("  GET 查询        ✅")
        print("  POST 热更       ✅ (min_score 0.3→0.5)")
        print("  超范围拒绝      ✅ (min_score=1.5 → 400)")
        print("  类型错误拒绝    ✅ (min_score='abc' → 400)")
        print("  未知键忽略      ✅ (unknown_key 被忽略)")
        print("  空请求体拒绝    ✅ (→ 400)")
        print("=" * 60)
    finally:
        # 清理 _SEM_API_OVERRIDE
        Orchestrator._SEM_API_OVERRIDE = None
        Orchestrator._clear_semantic_config_cache()


@pytest.mark.slow
def test_并发热更_配置热更不影响正在处理的请求(orch, monkeypatch):
    """模拟并发请求下的配置热更场景

    场景: 3 线程并发 process() + 1 线程热更 _SEM_API_OVERRIDE
    验证: 1. 无异常 2. 热更后新请求使用新配置
    """
    import threading
    import time as _time

    # 用 _SEM_API_OVERRIDE 设置初始配置（不 monkeypatch _load_semantic_layer_config）
    Orchestrator._SEM_API_OVERRIDE = {"enabled": True, "min_score": 0.3}
    Orchestrator._clear_semantic_config_cache()

    mock_svc = _make_mock_skills_mgmt_service(matches=[], instruction="")
    orch._call_llm = MagicMock(return_value="LLM 降级响应")

    errors = []
    process_count = [0]
    lock = threading.Lock()

    def worker_process():
        """模拟并发 process() 请求（patch 由主线程统一管理，见下方 with）"""
        try:
            for _ in range(50):
                orch.process("帮我写一首关于春天的诗")
                with lock:
                    process_count[0] += 1
        except Exception as e:
            with lock:
                errors.append(e)

    def worker_hot_reload():
        """模拟 API 热更配置（更新 _SEM_API_OVERRIDE）"""
        try:
            for i in range(5):
                Orchestrator._SEM_API_OVERRIDE = {"enabled": True, "min_score": 0.5 + i * 0.05}
                Orchestrator._clear_semantic_config_cache()
                _time.sleep(0.02)
        except Exception as e:
            with lock:
                errors.append(e)

    # 【不易】patch 必须在主线程 start/stop：子线程内 `with patch(...)` 的
    # start/stop 竞态会把 IntentRouter.classify 泄漏为 MagicMock，导致后续
    # 测试 classify 恒返回 unknown（response_workflows 17 失败根因）。
    with patch("agent.state_manager.get_skills_mgmt_service", return_value=mock_svc), \
         patch("agent.response_workflows.IntentRouter.classify",
               return_value=("unknown", _ConfidenceLow())), \
         patch("agent.response_workflows.ResponseTemplates.for_intent",
               return_value=None), \
         patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
               return_value=False), \
         patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
               return_value=False), \
         patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
               return_value=[]), \
         patch("agent.orchestrator.dialog_state.get_dialog_state",
               return_value=MagicMock(last_keywords=None,
                                       resolve=MagicMock(return_value=None))):
        # 启动 3 个 process 线程 + 1 个热更线程
        threads = [threading.Thread(target=worker_process) for _ in range(3)]
        threads.append(threading.Thread(target=worker_hot_reload))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # 验证无异常
    assert len(errors) == 0, "并发热更产生异常: %s" % errors
    assert process_count[0] == 150, "process 调用次数不符: %d" % process_count[0]

    # 验证热更生效（最终 min_score >= 0.5）
    final_config = Orchestrator._load_semantic_layer_config()
    assert final_config["min_score"] >= 0.5, "热更未生效: min_score=%s" % final_config["min_score"]

    print("\n" + "=" * 60)
    print(" 并发热更测试: 通过")
    print("=" * 60)
    print("  并发线程      : 3 process + 1 hot_reload")
    print("  process 调用  : %d 次 (无异常)" % process_count[0])
    print("  热更次数      : 5 次 (min_score 0.3→0.5→0.55→...→0.7)")
    print("  最终 min_score: %.2f (热更生效)" % final_config["min_score"])
    print("  异常数        : %d" % len(errors))
    print("=" * 60)

    # 清理
    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._clear_semantic_config_cache()


@pytest.mark.slow
def test_高并发_频繁热更无线程竞争(orch, monkeypatch):
    """模拟线上高并发: 5 线程频繁热更 + 5 线程并发 process()

    验证: 1. 无线程竞争异常 2. 配置一致性（min_score 始终在 [0,1] 范围）
    """
    import threading
    import random
    import time as _time
    import logging as _logging
    # 【不易】不能用 logging.disable()：它是进程级全局开关（Manager.disable），
    # 若本测试断言失败（如线程竞争异常）走不到末尾的恢复调用，会泄漏屏蔽
    # 后续所有 < WARNING 日志 → perf_monitor 等依赖 INFO 级 filter 链的测试
    # 静默失败（filter 永不触发，2026-08-13 全量 7 failed 根因）。monkeypatch
    # setattr 在测试结束（无论成败）自动恢复属性原值，等价且防泄漏。
    monkeypatch.setattr(_logging.root.manager, "disable", _logging.WARNING)

    Orchestrator._SEM_API_OVERRIDE = {"enabled": True, "min_score": 0.3}
    Orchestrator._clear_semantic_config_cache()

    mock_svc = _make_mock_skills_mgmt_service(matches=[], instruction="")
    orch._call_llm = MagicMock(return_value="LLM 降级响应")

    errors = []
    config_violations = []
    process_count = [0]
    reload_count = [0]
    lock = threading.Lock()

    def worker_process():
        try:
            for _ in range(50):
                orch.process("帮我写一首关于春天的诗")
                # 检查配置一致性（min_score 应始终在 [0,1] 范围）
                cfg = Orchestrator._load_semantic_layer_config()
                if not (0.0 <= cfg["min_score"] <= 1.0):
                    with lock:
                        config_violations.append(cfg["min_score"])
                with lock:
                    process_count[0] += 1
        except Exception as e:
            with lock:
                errors.append(e)

    def worker_hot_reload():
        try:
            for _ in range(50):
                new_score = round(random.uniform(0.1, 0.9), 3)
                Orchestrator._SEM_API_OVERRIDE = {"enabled": True, "min_score": new_score}
                Orchestrator._clear_semantic_config_cache()
                with lock:
                    reload_count[0] += 1
        except Exception as e:
            with lock:
                errors.append(e)

    # 【不易】patch 必须在主线程 start/stop：子线程内 `with patch(...)` 的
    # start/stop 竞态会把 IntentRouter.classify 泄漏为 MagicMock（同 test_并发热更）。
    with patch("agent.state_manager.get_skills_mgmt_service", return_value=mock_svc), \
         patch("agent.response_workflows.IntentRouter.classify",
               return_value=("unknown", _ConfidenceLow())), \
         patch("agent.response_workflows.ResponseTemplates.for_intent",
               return_value=None), \
         patch("agent.orchestrator.message_handler.MessageHandler.is_follow_up",
               return_value=False), \
         patch("agent.orchestrator.message_handler.MessageHandler.detect_dissatisfaction",
               return_value=False), \
         patch("agent.orchestrator.message_handler.MessageHandler.extract_keywords",
               return_value=[]), \
         patch("agent.orchestrator.dialog_state.get_dialog_state",
               return_value=MagicMock(last_keywords=None,
                                       resolve=MagicMock(return_value=None))):
        # 5 process 线程 + 5 热更线程 = 10 线程并发
        threads = [threading.Thread(target=worker_process) for _ in range(5)]
        threads += [threading.Thread(target=worker_hot_reload) for _ in range(5)]
        t_start = _time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        t_elapsed = _time.perf_counter() - t_start

    print("\n" + "=" * 60)
    print(" 高并发测试: 频繁热更 + 并发 process")
    print("=" * 60)
    print("  并发线程      : 5 process + 5 hot_reload (共 10)")
    print("  process 调用  : %d 次" % process_count[0])
    print("  热更次数      : %d 次 (随机 min_score 0.1~0.9)" % reload_count[0])
    print("  总耗时        : %.2f s" % t_elapsed)
    print("  线程竞争异常  : %d %s" % (len(errors), "✅ 无" if not errors else "❌ " + str(errors[:3])))
    print("  配置不一致    : %d %s" % (len(config_violations), "✅ 无" if not config_violations else "❌ " + str(config_violations[:3])))
    print("=" * 60)

    assert len(errors) == 0, "线程竞争异常: %s" % errors
    assert len(config_violations) == 0, "配置不一致: %s" % config_violations
    assert process_count[0] == 250
    assert reload_count[0] == 250

    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._clear_semantic_config_cache()
    # 注：manager.disable 由 monkeypatch 自动恢复（见上方 setattr 注释），无需手动复位
