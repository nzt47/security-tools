"""真实进化评估体系单元测试（任务 EVO-T2）

覆盖验收条件（执行步骤 7）:
    1. 抽象接口契约（SkillEvaluator 协议 / EvaluationResult 结构对齐谱系）
    2. 样本池加载（目录/单文件/多文件合并/坏文件容错）
    3. 真实执行评估（stub runner 返回真实 success/latency）
    4. 无样本分支（status=no_samples，绝不伪造指标）
    5. 分阶段初筛/全量逻辑（stage1 淘汰不进入 stage2）
    6. 预算熔断（budget_exceeded）
    7. LLM 评估降级路径（llm_client 不可用 → degraded，不伪造）
"""
import json
from pathlib import Path

import pytest

from agent.skills_mgmt.evaluator import (
    EvalSample,
    EvalSamplePool,
    EvaluationResult,
    EvaluatorRegistry,
    ExecOutcome,
    LlmEvaluator,
    OutputChecker,
    SampleEvaluation,
    SelfConsistencyScorer,
    SkillExecutorEvaluator,
    StagedEvaluator,
    TokenBudget,
    get_default_evaluator,
    resolve_category,
)


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

def make_sample(sid: str, category: str = "search", *,
                task: str = "查询测试", expected=None, **meta):
    return EvalSample(
        id=sid, category=category, task=task,
        expected_output=expected, metadata=meta,
    )


def make_skill(sid: str, *, tags=None, category="custom",
               params=None, version="1.0.0", metrics=None):
    """构造最小 Skill 对象（兼容 evaluator 只读字段）"""
    from agent.skills_mgmt.models import Skill, SkillCategory, SkillMetrics, SkillStatus, ContentType
    return Skill(
        id=sid,
        name=f"skill-{sid}",
        description="test",
        category=SkillCategory(category) if isinstance(category, str)
        else SkillCategory.CUSTOM,
        status=SkillStatus.APPROVED,
        enabled=True,
        version=version,
        content_type=ContentType.MARKDOWN,
        default_params=params or {},
        tags=tags or [],
        metrics=metrics or SkillMetrics(
            usage_count=10, success_count=7, failure_count=3,
            success_rate=0.7, avg_latency_ms=2000, p95_latency_ms=3000,
        ),
    )


def make_runner(success=True, result=None, latency_ms=10.0, *,
                stderr="", timed_out=False, fail_on_sid=None):
    """构造 stub runner（真实执行证据模拟：成功/失败/超时可控）"""
    def _run(skill, params):
        sid = getattr(skill, "id", "")
        if fail_on_sid is not None and sid == fail_on_sid:
            return ExecOutcome(success=False, exit_code=1, stderr="脚本不存在",
                               duration_ms=latency_ms)
        if timed_out:
            return ExecOutcome(success=True, timed_out=True, duration_ms=latency_ms)
        return ExecOutcome(success=success, exit_code=0,
                           result=result if result is not None else
                           {"answer": "查询结果: " + str(params.get("task", ""))},
                           stdout="ok", duration_ms=latency_ms)
    return _run


def make_pool(tmp_path, category_samples):
    """构造临时样本池：{category: [EvalSample]} → 写文件 + 返回 pool"""
    base = Path(tmp_path) / "evals"
    for cat, samples in category_samples.items():
        d = base / cat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cat}.json").write_text(
            json.dumps([s.to_dict() for s in samples], ensure_ascii=False),
            encoding="utf-8")
    return EvalSamplePool(base_dir=str(base))


# ════════════════════════════════════════════════════════════
#  1. 抽象接口契约 / 数据结构对齐
# ════════════════════════════════════════════════════════════

class TestContract:
    def test_eval_sample_fields(self):
        s = make_sample("s1", expected={"type": "contains", "values": ["云枢"]})
        assert s.id == "s1"
        assert s.category == "search"
        assert s.expected_output["type"] == "contains"

    def test_evaluation_result_status_no_samples(self):
        r = EvaluationResult(skill_id="s", status="no_samples",
                             notes=["无样本"])
        assert r.status == "no_samples"
        assert r.score == 0.0
        assert r.dimensions["success_rate"] == 0.0

    def test_evaluation_result_to_dict_aligns_lineage(self):
        """to_eval_result_dict 与 EvolutionRecord.eval_result 结构对齐"""
        r = EvaluationResult(
            skill_id="s", status="completed",
            success_rate=0.8, latency_ms=1000, satisfaction=0.5,
            sample_count=5,
        )
        d = r.to_eval_result_dict()
        # 谱系 EvolutionRecord.eval_result 所需字段（兼容：仅新增 status）
        assert d["score"] == pytest.approx(r.score)
        assert set(d["dimensions"]) == {"success_rate", "latency_norm", "satisfaction"}
        assert d["sample_count"] == 5
        assert d["evaluator_version"]
        assert d["status"] == "completed"

    def test_score_formula_matches_evolver(self):
        """score 公式与 offline_evolver._evaluate 一致（0.5/0.3/0.2）"""
        r = EvaluationResult(success_rate=1.0, latency_ms=0, satisfaction=1.0)
        # latency_norm = 1 - 0/5000 = 1 → score = 0.5+0.3+0.2 = 1.0
        assert r.score == pytest.approx(1.0)
        r2 = EvaluationResult(success_rate=0.0, latency_ms=5000, satisfaction=0.0)
        assert r2.score == pytest.approx(0.0)

    def test_protocol_evaluate_signature(self):
        """SkillEvaluator 协议实现必须可 evaluate(skill, sample_ids, params, budget)"""
        from typing import get_type_hints
        sig = get_type_hints(SkillExecutorEvaluator.evaluate)
        assert "params" in sig and "budget_tokens" in sig


# ════════════════════════════════════════════════════════════
#  2. 样本池加载
# ════════════════════════════════════════════════════════════

class TestSamplePool:
    def test_load_directory(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a"), make_sample("b")]})
        samples = pool.load_category("search")
        assert len(samples) == 2

    def test_load_single_file(self, tmp_path):
        base = Path(tmp_path) / "evals"
        base.mkdir(parents=True)
        (base / "code.json").write_text(
            json.dumps([make_sample("c1", "code").to_dict()]), encoding="utf-8")
        pool = EvalSamplePool(base_dir=str(base))
        assert len(pool.load_category("code")) == 1

    def test_multi_file_merge_and_dedup(self, tmp_path):
        base = Path(tmp_path) / "evals"
        d = base / "chat"
        d.mkdir(parents=True)
        (d / "a.json").write_text(json.dumps([
            make_sample("x", "chat").to_dict(), make_sample("y", "chat").to_dict()]))
        (d / "b.json").write_text(json.dumps([
            make_sample("x", "chat").to_dict(), make_sample("z", "chat").to_dict()]))
        pool = EvalSamplePool(base_dir=str(base))
        # 同 id 去重（后者覆盖），共 3 个唯一 id
        assert {s.id for s in pool.load_category("chat")} == {"x", "y", "z"}

    def test_corrupt_file_returns_empty(self, tmp_path):
        base = Path(tmp_path) / "evals"
        d = base / "search"
        d.mkdir(parents=True)
        (d / "bad.json").write_text("{ not json", encoding="utf-8")
        pool = EvalSamplePool(base_dir=str(base))
        assert pool.load_category("search") == []  # 损坏 → 无样本，不崩溃

    def test_missing_category_no_samples(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a")]})
        assert pool.has_samples("search")
        assert not pool.has_samples("nonexistent")

    def test_categories_lists_dirs(self, tmp_path):
        pool = make_pool(tmp_path, {
            "search": [make_sample("a")], "code": [make_sample("c", "code")],
        })
        assert set(pool.categories()) == {"search", "code"}


# ════════════════════════════════════════════════════════════
#  3. 输出校验器
# ════════════════════════════════════════════════════════════

class TestOutputChecker:
    def test_exact(self):
        ok, reason = OutputChecker.check(42, {"type": "exact", "value": 42})
        assert ok and reason == "exact"
        ok, _ = OutputChecker.check(43, {"type": "exact", "value": 42})
        assert not ok

    def test_contains(self):
        ok, _ = OutputChecker.check("云枢 Digital Life 定义",
                                    {"type": "contains", "values": ["云枢"]})
        assert ok
        ok, _ = OutputChecker.check("无关文本", {"type": "contains", "values": ["云枢"]})
        assert not ok

    def test_json_key(self):
        ok, _ = OutputChecker.check({"found": True}, {"type": "json", "key": "found", "value": True})
        assert ok
        ok, _ = OutputChecker.check({"found": False}, {"type": "json", "key": "found", "value": True})
        assert not ok

    def test_validator(self):
        ok, _ = OutputChecker.check(15, {"type": "validator",
                                         "expression": "result == 15"})
        assert ok

    def test_validator_real_sample_expression(self):
        """data/evals code 样本的 isinstance expression 必须可判定（白名单内置）"""
        ok, reason = OutputChecker._safe_validator(
            15, "isinstance(result, (int, float)) and result == 15")
        assert ok and reason == "validator"
        ok, _ = OutputChecker._safe_validator(
            "HELLO", "isinstance(result, str) and result == 'HELLO'")
        assert ok
        ok, _ = OutputChecker._safe_validator(
            14, "isinstance(result, (int, float)) and result == 15")
        assert not ok

    def test_validator_whitelist_no_io_escape(self):
        """白名单不放行 I/O：__ 属性访问 / open( 仍被阻断（守不易）"""
        ok, reason = OutputChecker._safe_validator("x", "result.__class__")
        assert not ok and "blocked" in reason
        ok, reason = OutputChecker._safe_validator("x", "open('/etc/passwd')")
        assert not ok and "blocked" in reason
        ok, reason = OutputChecker._safe_validator("x", "import os")
        assert not ok and "blocked" in reason

    def test_validator_blocked_forbidden_tokens(self):
        # 受限 eval 安全：禁 import / __ / 分号 / open( / eval( / exec(
        for expr in ("import os", "open('/etc/passwd')", "eval('1')", "result.__class__"):
            ok, reason = OutputChecker._safe_validator("x", expr)
            assert not ok and "blocked" in reason

    def test_no_checker_fails(self):
        ok, reason = OutputChecker.check("anything", None)
        assert not ok and reason == "no_checker"


# ════════════════════════════════════════════════════════════
#  4. 预算熔断
# ════════════════════════════════════════════════════════════

class TestTokenBudget:
    def test_spend_within_budget(self):
        b = TokenBudget(budget_tokens=100)
        assert b.spend(50)
        assert not b.exceeded
        assert b.used == 50

    def test_spend_exceeds_budget(self):
        b = TokenBudget(budget_tokens=100)
        assert b.spend(50)
        assert not b.spend(60)  # 110 > 100 → 熔断
        assert b.exceeded

    def test_estimate_positive(self):
        assert TokenBudget.estimate("hello world") >= 1
        assert TokenBudget.estimate() >= 1  # 固定样本开销


# ════════════════════════════════════════════════════════════
#  5. 真实执行评估（SkillExecutorEvaluator）
# ════════════════════════════════════════════════════════════

class TestSkillExecutorEvaluator:
    def test_real_eval_success(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", task="查询云枢", expected={"type": "contains", "values": ["云枢"]},
            input={"query": "云枢"})]})
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(result={"answer": "云枢是什么"}))
        skill = make_skill("search-opt", tags=["search"])
        r = ev.evaluate(skill)
        assert r.status == "completed"
        assert r.success_rate == 1.0
        assert r.sample_count == 1
        assert r.latency_ms > 0  # 真实延迟
        assert r.samples[0].checked_by == "contains"

    def test_real_eval_wrong_output_zero_success(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", task="查询云枢", expected={"type": "contains", "values": ["云枢"]},
            input={"query": "云枢"})]})
        # 输出不含关键词 → 真实失败
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(result={"answer": "完全无关的回复"}))
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        assert r.status == "completed"
        assert r.success_rate == 0.0

    def test_exec_failure_skipped_not_fabricated(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", task="查询", expected={"type": "contains", "values": ["x"]})]})
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(fail_on_sid="search-opt"))
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        # 执行失败 → 样本 skipped，绝不伪造通过
        assert r.samples[0].skipped
        assert "跳过" in r.notes[0]

    def test_timed_out_skipped(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("q1", task="查询")]})
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(timed_out=True))
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        assert r.samples[0].skipped
        assert r.samples[0].error == "执行超时"

    def test_no_samples_returns_no_samples(self, tmp_path):
        """无样本分支：绝不伪造指标"""
        pool = make_pool(tmp_path, {"search": []})
        ev = SkillExecutorEvaluator(pool=pool, runner=make_runner())
        r = ev.evaluate(make_skill("some-skill"))
        assert r.status == "no_samples"
        assert r.success_rate == 0.0
        assert r.sample_count == 0

    def test_budget_break(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [
            make_sample(f"q{i}", task="查询" * 50) for i in range(10)]})
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(), budget_tokens=1)  # 极小预算
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        assert r.status == "budget_exceeded"
        assert r.budget_exceeded

    def test_params_override_passed_to_runner(self, tmp_path):
        captured = {}
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", task="查询", input={"query": "云枢"})]})

        def _run(skill, params):
            captured["params"] = dict(params)
            return ExecOutcome(success=True, result={"ok": True}, duration_ms=1)
        ev = SkillExecutorEvaluator(pool=pool, runner=_run)
        ev.evaluate(make_skill("search-opt", tags=["search"]),
                    params={"threshold": 0.9})
        assert captured["params"]["threshold"] == 0.9
        assert captured["params"]["query"] == "云枢"  # metadata.input 并入
        assert captured["params"]["task"]  # task 注入

    def test_validator_disabled_by_default(self, tmp_path):
        pool = make_pool(tmp_path, {"code": [make_sample(
            "c1", "code", task="求和",
            expected={"type": "validator", "expression": "result == 15"})]})
        ev = SkillExecutorEvaluator(pool=pool, runner=make_runner(result=15))
        r = ev.evaluate(make_skill("code-opt", tags=["code"]))
        # 默认 allow_validator=False → 校验器被禁用 → 跳过（不伪造）
        assert r.samples[0].skipped
        assert r.samples[0].checked_by == "unverifiable"

    def test_validator_enabled_when_allowed(self, tmp_path):
        pool = make_pool(tmp_path, {"code": [make_sample(
            "c1", "code", task="求和",
            expected={"type": "validator", "expression": "result == 15"})]})
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(result=15), allow_validator=True)
        r = ev.evaluate(make_skill("code-opt", tags=["code"]))
        assert r.samples[0].success
        assert r.samples[0].checked_by == "validator"


# ════════════════════════════════════════════════════════════
#  6. 自一致性 + 反馈信号（开放域替代验证）
# ════════════════════════════════════════════════════════════

class TestOpenDomainVerification:
    def test_self_consistency_identical_outputs(self):
        scorer = SelfConsistencyScorer()
        assert scorer.score(["同样的输出", "同样的输出"]) == pytest.approx(1.0)

    def test_self_consistency_different_outputs_lower(self):
        scorer = SelfConsistencyScorer()
        assert scorer.score(["完全不同的内容甲", "完全不同的内容乙"]) < 1.0

    def test_self_consistency_single_output(self):
        scorer = SelfConsistencyScorer()
        assert scorer.score(["单独一次输出"]) == pytest.approx(1.0)

    def test_chat_sample_uses_consistency(self, tmp_path):
        """开放域样本（无 expected_output）→ 自一致性路径"""
        pool = make_pool(tmp_path, {"chat": [make_sample(
            "d1", "chat", task="自我介绍", user_message="你好")]})
        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(result={"reply": "你好！我是云枢。"}),
            use_feedback=False, consistency_runs=3)
        r = ev.evaluate(make_skill("chat-opt", tags=["chat"]))
        assert r.samples[0].checked_by == "self_consistency"
        assert r.samples[0].score > 0  # 一致输出 → 高自一致性得分

    def test_feedback_signal_combined(self, tmp_path, monkeypatch):
        """反馈信号叠加：满意度 80% 参与评分"""
        pool = make_pool(tmp_path, {"chat": [make_sample(
            "d1", "chat", task="自我介绍", user_message="你好")]})

        class _FakeFeedback:
            def get_skill_feedback_summary(self, skill_id, days=30):
                return {"total_feedback": 10,
                        "satisfaction_rate_percent": 80.0}

        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(result={"reply": "你好！我是云枢。"}),
            feedback_manager=_FakeFeedback(), consistency_runs=3)
        r = ev.evaluate(make_skill("chat-opt", tags=["chat"]))
        se = r.samples[0]
        assert se.checked_by == "self_consistency+feedback"
        assert se.details["feedback_score"] == pytest.approx(0.8)

    def test_feedback_none_keeps_consistency(self, tmp_path):
        """无反馈数据 → 只走自一致性，不硬造反馈分"""
        pool = make_pool(tmp_path, {"chat": [make_sample(
            "d1", "chat", task="自我介绍", user_message="你好")]})

        class _NoFeedback:
            def get_skill_feedback_summary(self, skill_id, days=30):
                return None

        ev = SkillExecutorEvaluator(
            pool=pool, runner=make_runner(result={"reply": "你好！我是云枢。"}),
            feedback_manager=_NoFeedback(), consistency_runs=3)
        r = ev.evaluate(make_skill("chat-opt", tags=["chat"]))
        assert r.samples[0].checked_by == "self_consistency"


# ════════════════════════════════════════════════════════════
#  7. 分阶段评估（Staged Eval）
# ════════════════════════════════════════════════════════════

class TestStagedEvaluator:
    def test_stage1_eliminated_before_stage2(self, tmp_path, monkeypatch):
        """阶段1 得分低于阈值 → 淘汰，不进入阶段2（验收条件 3）"""
        calls = {"stage2": 0}
        samples = [make_sample(f"s{i}", task="查询云枢",
                               expected={"type": "contains", "values": ["云枢"]})
                   for i in range(10)]
        pool = make_pool(tmp_path, {"search": samples})

        # 阶段1 用的前 10%（1 条）返回低分输出；若进入阶段2 则会调用更多
        runner = make_runner(result={"answer": "无关回复"})  # contains 校验失败
        ev = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=runner),
            pool=pool, stage1_ratio=0.1, stage1_max_samples=1,
            stage1_min_score=0.5)
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        assert r.eliminated
        assert r.stage == "stage1"
        assert r.sample_count == 1  # 只评估了阶段1样本
        assert "淘汰" in r.notes[0]

    def test_stage2_runs_when_pass_stage1(self, tmp_path):
        """阶段1 通过 → 进入阶段2 全量评估"""
        samples = [make_sample(f"s{i}", task="查询云枢",
                               expected={"type": "contains", "values": ["云枢"]})
                   for i in range(10)]
        pool = make_pool(tmp_path, {"search": samples})
        runner = make_runner(result={"answer": "云枢 Digital Life"})
        ev = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=runner),
            pool=pool, stage1_ratio=0.1, stage1_max_samples=1,
            stage1_min_score=0.3)
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        assert not r.eliminated
        assert r.stage == "stage2"
        assert r.sample_count == 10  # 全量
        assert r.success_rate == 1.0

    def test_stage1_budget_break_stops(self, tmp_path):
        """阶段1 预算熔断 → 返回 budget_exceeded，不再继续"""
        samples = [make_sample(f"s{i}", task="查询" * 100) for i in range(10)]
        pool = make_pool(tmp_path, {"search": samples})
        ev = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=make_runner()),
            pool=pool, stage1_ratio=0.5, stage1_max_samples=5,
            stage1_budget_tokens=1)
        r = ev.evaluate(make_skill("search-opt", tags=["search"]))
        assert r.status == "budget_exceeded"
        assert r.stage == "stage1"

    def test_no_samples_in_stage(self, tmp_path):
        pool = make_pool(tmp_path, {"search": []})
        ev = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=make_runner()), pool=pool)
        r = ev.evaluate(make_skill("search-opt"))
        assert r.status == "no_samples"

    def test_explicit_sample_ids_skip_stage1(self, tmp_path):
        """显式指定 sample_ids → 跳过初筛直接评估该子集（供基线复用）"""
        samples = [make_sample(f"s{i}", task="查询云枢",
                               expected={"type": "contains", "values": ["云枢"]})
                   for i in range(10)]
        pool = make_pool(tmp_path, {"search": samples})
        ev = StagedEvaluator(
            SkillExecutorEvaluator(pool=pool, runner=make_runner(result={"answer": "云枢"})),
            pool=pool)
        r = ev.evaluate(make_skill("search-opt", tags=["search"]),
                        sample_ids=["s0", "s1"])
        assert r.sample_count == 2
        assert r.stage == ""  # 未走分阶段标记


# ════════════════════════════════════════════════════════════
#  8. LLM 评估降级路径
# ════════════════════════════════════════════════════════════

class TestLlmEvaluator:
    def test_llm_unavailable_degraded(self, tmp_path):
        """LLM 客户端不可用 → status=degraded，全部跳过，绝不伪造（验收条件 5）"""
        pool = make_pool(tmp_path, {"general": [make_sample("g1", "general")]})
        ev = LlmEvaluator(pool=pool, llm_client=None)
        r = ev.evaluate(make_skill("custom-skill", category="custom"))
        assert r.status == "degraded"
        assert r.samples and all(s.skipped for s in r.samples)
        assert r.samples[0].checked_by == "llm_unavailable"

    def test_llm_judge_success(self, tmp_path):
        pool = make_pool(tmp_path, {"general": [make_sample("g1", "general")]})

        class _LLM:
            def chat(self, prompt, **kw):
                return '{"success": true, "score": 0.9}'

        ev = LlmEvaluator(pool=pool, llm_client=_LLM(),
                          runner=make_runner(result={"answer": "ok"}))
        r = ev.evaluate(make_skill("custom-skill", category="custom"))
        assert r.status == "completed"
        assert r.samples[0].success
        assert r.samples[0].checked_by == "llm"

    def test_llm_judge_failure(self, tmp_path):
        pool = make_pool(tmp_path, {"general": [make_sample("g1", "general")]})

        class _LLM:
            def chat(self, prompt, **kw):
                return '{"success": false, "score": 0.1}'

        ev = LlmEvaluator(pool=pool, llm_client=_LLM(),
                          runner=make_runner(result={"answer": "bad"}))
        r = ev.evaluate(make_skill("custom-skill", category="custom"))
        assert not r.samples[0].success


# ════════════════════════════════════════════════════════════
#  9. 评估器注册表 + 类别解析
# ════════════════════════════════════════════════════════════

class TestRegistry:
    def test_resolve_category_by_tags(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a")],
                                    "code": [make_sample("c", "code")]})
        assert resolve_category(make_skill("x", tags=["search"]), pool) == "search"
        assert resolve_category(make_skill("y", tags=["code"]), pool) == "code"

    def test_resolve_category_by_id_keyword(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a")],
                                    "chat": [make_sample("d", "chat")]})
        assert resolve_category(make_skill("my-search-skill"), pool) == "search"
        assert resolve_category(make_skill("dialog-chat-bot"), pool) == "chat"

    def test_resolve_category_fallback_general(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a")]})
        assert resolve_category(make_skill("unrelated-skill"), pool) == "general"

    def test_registry_default_search_uses_executor(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample(
            "q1", task="查询云枢", expected={"type": "contains", "values": ["云枢"]})]})
        ev = EvaluatorRegistry(pool=pool).get(
            make_skill("search-opt", tags=["search"]), staged=False)
        assert isinstance(ev, SkillExecutorEvaluator)

    def test_registry_unregistered_category_uses_llm(self, tmp_path):
        """未注册类别 → 分阶段 LLM 评估（默认降级）"""
        pool = make_pool(tmp_path, {"general": [make_sample("g1", "general")]})
        ev = EvaluatorRegistry(pool=pool).get(
            make_skill("custom-skill", category="custom"), staged=False)
        assert isinstance(ev, LlmEvaluator)

    def test_registry_get_returns_staged_by_default(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a")]})
        ev = EvaluatorRegistry(pool=pool).get(make_skill("search-opt", tags=["search"]))
        assert isinstance(ev, StagedEvaluator)

    def test_get_default_evaluator(self, tmp_path):
        pool = make_pool(tmp_path, {"search": [make_sample("a")]})
        ev = get_default_evaluator(make_skill("search-opt", tags=["search"]), pool=pool)
        assert isinstance(ev, StagedEvaluator)
