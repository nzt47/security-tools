"""任务6 单元测试：候选生成与筛选（selector.py）

验收 2：safety < 0.6 的候选不会出现在 selected_strategies
覆盖：安全红线淘汰、综合分排序取 top3、候选三来源（repair_hint/reuse/llm）。
"""

import pytest

from agent.evolution.defect_case import build_failure_case
from agent.evolution.selector import (
    SAFETY_RED_LINE,
    Strategy,
    composite_score,
    generate_candidates,
    generate_llm_candidates,
    select_strategies,
)


def _case(scores=None, task_type="code_repair", tool_name=None):
    case = build_failure_case(
        task_type=task_type,
        trace_id="tr-sel",
        diagnosis={"error_type": "network_timeout",
                   "error_message": "超时", "repair_hints": ["建议重试"]},
        task_succeeded=False,
        attempts=2,
    )
    if scores:
        case.scores.update(scores)
    return case


def _strategy(prompt, scores, scope="task_type:code_repair", **kw):
    return Strategy(
        strategy_id=kw.pop("strategy_id", "s-" + prompt[:4]),
        case_id="c1",
        prompt_patch=prompt,
        scope=scope,
        scores=dict(scores),
        **kw,
    )


class TestSafetyRedLine:
    """验收2：安全红线必淘汰"""

    def test_low_safety_candidate_excluded(self):
        case = _case()
        unsafe = _strategy("危险操作策略", {"safety": 0.1, "utility": 1.0,
                                          "trajectory_efficiency": 1.0,
                                          "over_rejection": 0.0})
        safe = _strategy("安全策略", {"safety": 1.0, "utility": 0.8,
                                      "trajectory_efficiency": 0.8,
                                      "over_rejection": 0.1})
        selected = select_strategies([unsafe, safe])
        assert all(s.scores["safety"] >= SAFETY_RED_LINE for s in selected)
        assert all(s.strategy_id != unsafe.strategy_id for s in selected)
        assert selected[0].strategy_id == safe.strategy_id

    def test_red_line_boundary_included(self):
        """边界：safety 恰好 == 红线 0.6 → 保留"""
        case = _case()
        boundary = _strategy("边界策略", {"safety": 0.6, "utility": 0.5,
                                          "trajectory_efficiency": 0.5,
                                          "over_rejection": 0.1})
        selected = select_strategies([boundary])
        assert len(selected) == 1

    def test_all_unsafe_yields_empty(self):
        case = _case()
        selected = select_strategies([
            _strategy("a", {"safety": 0.0, "utility": 1.0, "trajectory_efficiency": 1.0,
                            "over_rejection": 0.0}),
            _strategy("b", {"safety": 0.5, "utility": 1.0, "trajectory_efficiency": 1.0,
                            "over_rejection": 0.0}),
        ])
        assert selected == []


class TestCompositeScore:
    """综合分公式：0.4*safety + 0.3*utility + 0.2*traj + 0.1*(1-over_rejection)"""

    def test_formula(self):
        scores = {"safety": 1.0, "utility": 1.0,
                  "trajectory_efficiency": 1.0, "over_rejection": 0.0}
        assert composite_score(scores) == pytest.approx(1.0)
        scores["safety"] = 0.5
        # 0.4*0.5 + 0.3*1 + 0.2*1 + 0.1*1 = 0.2+0.3+0.2+0.1 = 0.8
        assert composite_score(scores) == pytest.approx(0.8)

    def test_sort_by_composite_top3(self):
        case = _case()
        candidates = [
            _strategy(f"p{i}", {"safety": 1.0, "utility": u,
                                "trajectory_efficiency": 1.0, "over_rejection": 0.0})
            for i, u in enumerate([0.9, 0.5, 0.7, 0.2, 0.8])
        ]
        selected = select_strategies(candidates, top_n=3)
        assert len(selected) == 3
        assert [c.prompt_patch for c in selected] == ["p0", "p4", "p2"]


class TestCandidateGeneration:
    """候选三来源：a) repair_hints  b) 相似复用  c) LLM"""

    def test_repair_hints_become_candidates(self):
        case = _case()
        candidates = generate_candidates(
            case, repair_hints=["提示1", "提示2"],
        )
        prompts = {c.prompt_patch for c in candidates}
        assert "提示1" in prompts and "提示2" in prompts
        assert all(c.scope == "task_type:code_repair" for c in candidates)
        assert all(c.source == "repair_hint" for c in candidates)

    def test_similar_strategy_reuse(self):
        case = _case()
        existing = Strategy(
            strategy_id="s-reuse-1", case_id="old",
            prompt_patch="旧策略", scope="task_type:code_repair",
            scores=dict(case.scores),
        )
        candidates = generate_candidates(case, similar_strategies=[existing])
        assert any(c.prompt_patch == "旧策略" and c.source == "reuse"
                   for c in candidates)

    def test_deprecated_similar_not_reused(self):
        case = _case()
        old = Strategy(
            strategy_id="s-dep-1", case_id="old",
            prompt_patch="已废弃策略", scope="task_type:code_repair",
            status="deprecated", scores=dict(case.scores),
        )
        candidates = generate_candidates(case, similar_strategies=[old])
        assert all(c.prompt_patch != "已废弃策略" for c in candidates)

    @pytest.mark.asyncio
    async def test_llm_candidates_optional(self):
        """来源 c：LLM 候选（异步生成，异常降级为空）"""
        case = _case()

        async def gen(c):
            return ["LLM 策略1", "LLM 策略2"]

        candidates = await generate_llm_candidates(case, gen)
        assert len(candidates) == 2
        assert all(c.source == "llm" for c in candidates)

        async def boom(c):
            raise RuntimeError("llm down")

        assert await generate_llm_candidates(case, boom) == []
