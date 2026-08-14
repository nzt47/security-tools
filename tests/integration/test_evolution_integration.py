"""任务6 集成测试：运行时注入接线 + auto_tuner 参数联动

验收 3：injector 命中时 prompt 含 [策略 #id] 且日志记录 strategy_id
验收 5：auto_tuner 能读取 get_strategy_stats() 且生成建议走 HITL 审批
覆盖：
  - ReActLoop._think 注入策略段落（真实 _think 路径，mock LLM 捕获 prompt）
  - CriticEvaluator.evaluate 注入策略到 feedback
  - tool_router.get_tools_for_input 备用路径策略生效
  - auto_tuner.generate_strategy_linked_suggestion → pending → approve → apply
"""

import json

import pytest
from unittest.mock import AsyncMock

from agent.auto_tuner import AutoTuner
from agent.evolution.defect_case import build_failure_case
from agent.evolution.injector import StrategyInjector
from agent.evolution.selector import Strategy


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def inj(tmp_path, monkeypatch):
    """tmp 隔离注入器 + 全局 get_injector 指向它（避免触碰真实 data/evolution）"""
    instance = StrategyInjector(storage_path=str(tmp_path / "evolution"))
    monkeypatch.setattr(
        "agent.evolution.injector.get_injector",
        lambda required=False: instance,
    )
    return instance


def _seed_strategy(inj, *, prompt_patch, scope, param_patch=None):
    """直接入库一条策略（绕过筛选，聚焦注入行为）"""
    s = Strategy(
        strategy_id=f"sid-{abs(hash(prompt_patch)) % 10**6}",
        case_id="c-seed",
        prompt_patch=prompt_patch,
        param_patch=param_patch or {},
        scope=scope,
    )
    inj._strategies.append(s)
    inj._save()
    return s.strategy_id


# ═══════════════════════════════════════════════════════════════
#  验收3：ReActLoop._think 策略注入
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_react_think_injects_strategy_with_id(inj, caplog):
    """验收3：命中策略时 prompt 含 [策略 #id] 且日志记录 strategy_id"""
    from planning.react import ReActLoop

    sid = _seed_strategy(inj, prompt_patch="避免无限重试网络工具",
                         scope="task_type:general")  # classify_task("网络检索任务") → general

    captured = {}

    async def _chat(messages):
        captured["prompt"] = messages[0]["content"]
        return json.dumps({"reasoning": "ok", "action_type": "finish",
                           "result": "完成"})

    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = _chat
    planner = type("P", (), {})()
    planner.llm = mock_llm
    planner.tool_registry = type("T", (), {"list_tools": lambda self: []})()

    loop = ReActLoop(planner, reflector=None, max_iterations=3)
    import logging
    with caplog.at_level(logging.INFO, logger="agent.evolution"):
        result = await loop._think("网络检索任务", {}, [])
        assert result is not None

    assert f"[策略 #{sid}]" in captured["prompt"]
    assert "避免无限重试网络工具" in captured["prompt"]
    assert f"策略命中注入: {sid}" in caplog.text


# ═══════════════════════════════════════════════════════════════
#  Critic 注入
# ═══════════════════════════════════════════════════════════════

def test_critic_evaluate_injects_strategy_feedback(inj, monkeypatch):
    """Critic 规则模式：策略进入 feedback（注明 [策略 #id]）"""
    from agent.cognitive.critic import CriticEvaluator

    sid = _seed_strategy(inj, prompt_patch="避免过度拒绝",
                         scope="critic")

    evaluator = CriticEvaluator(threshold=70)
    # 熔断器/降级依赖外部单例，规则模式走真实路径需正常评分；
    # 注入上下文在 evaluate 内部完成，直接断言 feedback 含策略
    result = evaluator.evaluate(
        user_query="请帮我写一段代码",
        response="我无法完成这个请求，因为权限不足",
        context={},
    )
    strategy_fb = [f for f in result.feedback if f"[策略 #{sid}]" in f]
    assert strategy_fb, "Critic feedback 应包含注入策略"
    assert "避免过度拒绝" in strategy_fb[0]


# ═══════════════════════════════════════════════════════════════
#  tool_router 备用路径注入
# ═══════════════════════════════════════════════════════════════

def test_tool_router_appends_fallback_tools(inj, monkeypatch):
    """工具路由：策略 param_patch.fallback_tools 补入结果"""
    from agent import tool_router as tr

    _seed_strategy(
        inj,
        prompt_patch="web_search 失败率高，改用备用路径",
        scope="tool:web_search",
        param_patch={"fallback_tools": ["web_scrape"]},
    )
    # web_search 必须是被路由选中的工具（关键词分类命中）
    tools = tr.get_tools_for_input("帮我搜索最新新闻", max_tools=50)
    assert "web_scrape" in tools


# ═══════════════════════════════════════════════════════════════
#  验收5：auto_tuner 参数联动 + HITL
# ═══════════════════════════════════════════════════════════════

def test_auto_tuner_strategy_linked_suggestion_hitl(inj, tmp_path):
    """验收5：auto_tuner 读取 get_strategy_stats() 生成建议并走 HITL 审批"""
    sid = _seed_strategy(
        inj,
        prompt_patch="web_search 高失败率",
        scope="tool:web_search",
        param_patch={},
    )
    # 高失败率统计：尝试 4 次、成功 0 → rate=0.0 <= 0.5 且 attempt>=3
    for _ in range(4):
        inj.record_strategy_result(sid, success=False)

    tuner = AutoTuner(storage_path=str(tmp_path / "auto_tuning"))
    tuner.initialize()

    suggestion = tuner.generate_strategy_linked_suggestion()
    assert suggestion is not None
    assert suggestion.status == "pending"
    assert suggestion.metadata.get("source") == "evolution"
    assert "tool_max_concurrency" in suggestion.proposed_params

    # HITL 审批链：approve → apply（apply 返回含快照的 dict）
    tuner.approve_suggestion(suggestion.suggestion_id, reviewer="tester")
    applied = tuner.apply_suggestion(suggestion.suggestion_id)
    assert isinstance(applied, dict) and "snapshot_id" in applied


def test_auto_tuner_strategy_suggestion_none_without_high_failure(inj, tmp_path):
    """无高失败工具 → 不生成联动建议"""
    sid = _seed_strategy(inj, prompt_patch="正常策略", scope="tool:ok_tool")
    inj.record_strategy_result(sid, success=True)  # attempt=1 < 3

    tuner = AutoTuner(storage_path=str(tmp_path / "auto_tuning"))
    tuner.initialize()
    assert tuner.generate_strategy_linked_suggestion() is None


def test_auto_tuner_strategy_weekly_report(inj, tmp_path):
    """进化周报复用 auto_tuner 报告机制（TuningReport 入库）"""
    sid = _seed_strategy(inj, prompt_patch="周报策略", scope="task_type:general")
    inj.record_strategy_result(sid, success=True)

    tuner = AutoTuner(storage_path=str(tmp_path / "auto_tuning"))
    tuner.initialize()
    report = tuner.generate_strategy_weekly_report()
    assert report is not None
    assert report.objective == "evolution"
    assert report.metrics_summary["week_failure_cases"] == 0
    assert report.metrics_summary["week_strategy_hits"] == 1
