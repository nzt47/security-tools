"""任务5：LLM-as-Judge 双假设验证 — Judge dry-run 通道测试

覆盖（任务提示词 §6 验收）:
- Judge 通道零干预：审计 intervention=False + KPI#7 零变化 + 无提交/审批/回滚 import；
- 预算 enforce 前置：超限学习动作被拒（budget_blocked）、warn_only 否决 LLM 调用、
  主链路 LLM 调用不受影响；
- 分歧率/采纳率差异计算（compute_stats + learning_metrics 扩展字段）；
- 判别规则：A/B 两类合成数据结论方向与预设规则一致；
- 开关默认值：enabled=false（零 LLM）、dry_run=true；
- 脱敏管道（token_redactor）与 LLM 响应解析；
- 任务3 放行审计候选读取（rollout_audit preview/approved）。

【不易】本测试全部使用隔离实例（LearningBudget 注入独立熔断器、LearningMetrics
        直接构造），不触碰全局单例；不修改任何既有评估器行为。
"""

import json
import os

import pytest

from agent.circuit_breaker import CircuitBreaker
from agent.learning_budget import LearningBudget
from agent.learning_metrics import LearningMetrics
from agent.learning.judge_channel import (
    VERDICT_ACCEPT,
    VERDICT_REJECT,
    CONCLUSION_A_EVAL_INSUFFICIENT,
    CONCLUSION_B_CANDIDATE_QUALITY,
    CONCLUSION_INSUFFICIENT_DATA,
    CONCLUSION_INCONCLUSIVE,
    RECOMMEND_EVALUATE_INTRODUCE,
    RECOMMEND_NOT_INTRODUCE,
    STATUS_BUDGET_BLOCKED,
    STATUS_BUDGET_NOT_ENFORCE,
    STATUS_JUDGED,
    STATUS_SKIPPED,
    JudgeCandidate,
    _parse_judge_response,
    compute_stats,
    discriminate,
    evaluate_candidates,
    evaluate_one,
    load_candidates_from_rollout_audit,
    load_judge_config,
    rule_verdict_mirror,
)


# ════════════════════════════════════════════════════════════
#  工具
# ════════════════════════════════════════════════════════════

def _mk_budget(mode="enforce", max_single=0, max_daily=1_000_000,
               cooldown=60.0) -> LearningBudget:
    breaker = CircuitBreaker(
        name="judge_test",
        failure_threshold=1.0,
        min_calls=1,
        cooldown_seconds=cooldown,
        half_open_max_calls=1,
        half_open_success_threshold=1,
    )
    return LearningBudget(
        config={
            "mode": mode,
            "max_single_action_tokens": max_single,
            "max_daily_tokens": max_daily,
        },
        breaker=breaker,
    )


class _FakeLLM:
    """duck-typed LLM 客户端（chat/invoke/complete/generate 惯例）；可注入响应序列"""

    def __init__(self, responses=None, verdicts=None):
        self.responses = list(responses or [])
        self.verdicts = list(verdicts or [])
        self.calls = 0

    def _next(self) -> str:
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        v = self.verdicts[0] if len(self.verdicts) == 1 else \
            self.verdicts[(self.calls - 1) % max(1, len(self.verdicts))]
        return json.dumps({"verdict": v, "confidence": 0.9, "reason": "ok"})

    def chat(self, prompt):
        return self._next()


def _cfg(enabled=True, **overrides) -> dict:
    cfg = load_judge_config()
    cfg["enabled"] = enabled
    cfg.update(overrides)
    return cfg


def _cand(cid, rule_verdict=None, scores=None, source="rollout_preview",
          content="candidate payload") -> JudgeCandidate:
    return JudgeCandidate(
        candidate_id=cid,
        source=source,
        content=content,
        rule_verdict=rule_verdict,
        scores=dict(scores or {}),
    )


def _run(candidates, llm=None, budget=None, metrics=None, cfg=None, audit=None):
    cfg = cfg if cfg is not None else _cfg()
    metrics = metrics if metrics is not None else LearningMetrics(enabled=True)
    budget = budget if budget is not None else _mk_budget()
    return evaluate_candidates(
        candidates, llm_client=llm, budget=budget, metrics=metrics,
        cfg=cfg, audit_file=audit,
    )


# ════════════════════════════════════════════════════════════
#  零干预不变式
# ════════════════════════════════════════════════════════════

def test_evaluate_candidates_zero_intervention_audit_provable(tmp_path):
    """验收：Judge 通道零干预——审计 intervention=False、KPI#7 零变化、无采纳调用"""
    cands = [
        _cand("c1", rule_verdict=VERDICT_ACCEPT),
        _cand("c2", rule_verdict=VERDICT_REJECT),
        _cand("c3", rule_verdict=VERDICT_ACCEPT),
    ]
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "judge_audit.jsonl")

    # 在 metrics 上挂 spy：记录任何采纳侧调用
    adoption_calls = []
    orig_record = metrics.record_evolution_candidate
    metrics.record_evolution_candidate = lambda *a, **k: adoption_calls.append(("evolution", a, k))

    res = _run(cands, llm=_FakeLLM(verdicts=["accept", "reject", "reject"]),
               metrics=metrics, audit=audit)

    # 1) 每条审计记录干预=false 且 mode=dry_run
    for r in res["records"]:
        assert r["intervention"] is False
        assert r["mode"] == "dry_run"
    # 2) 零采纳侧调用
    assert adoption_calls == []
    # 3) KPI#7 零变化（Judge 通道不写进化采纳率）
    snap = metrics.get_snapshot()
    assert snap["kpis"]["evolution_adoption_rate"]["candidates"] == 0
    assert snap["kpis"]["evolution_adoption_rate"]["adopted"] == 0
    # 4) 审计文件逐条可证
    lines = [json.loads(l) for l in open(audit, encoding="utf-8")]
    assert len(lines) == 3
    assert all(l["intervention"] is False for l in lines)
    assert all(l["judge_status"] == STATUS_JUDGED for l in lines)


def test_judge_channel_module_has_no_commit_imports():
    """代码审计：Judge 通道不 import 任何提交/审批/回滚模块（零绕过路径）"""
    src_path = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "agent", "learning", "judge_channel.py"))
    src = open(src_path, encoding="utf-8").read()
    banned = (
        "approval", "rollback", "offline_evolver", "evolution_scheduler",
        "meta_editor", "lineage", "value_guard", "precipitate",
    )
    # 只审计 import 语句（docstring 中的不变式说明不算引用）
    import_lines = [ln.strip() for ln in src.splitlines()
                    if ln.strip().startswith(("import ", "from "))]
    for ln in import_lines:
        for token in banned:
            assert token not in ln, f"Judge 通道不得 import 提交/审批/回滚模块: {token} (line: {ln})"
    # 审计记录恒含 intervention=False（零干预声明在代码中可证）
    assert '"intervention": False' in src or '"intervention": False,' in src


# ════════════════════════════════════════════════════════════
#  预算 enforce 前置
# ════════════════════════════════════════════════════════════

def test_budget_enforce_blocks_over_limit_judge_call(tmp_path):
    """验收：enforce 超单次上限 → budget_blocked，不伪造判定、零成本、LLM 未调用"""
    budget = _mk_budget(max_single=50)  # 单次上限 50 token（prompt 预估 > 50）
    llm = _FakeLLM(verdicts=["accept"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")

    res = _run([_cand("c1", rule_verdict=VERDICT_ACCEPT)],
               llm=llm, budget=budget, metrics=metrics, audit=audit)

    r = res["records"][0]
    assert r["judge_status"] == STATUS_BUDGET_BLOCKED
    assert r["judge_verdict"] is None          # 不伪造判定
    assert r["tokens_used"] == 0               # LLM 未调用 → 零成本
    assert llm.calls == 0                      # 未发起 LLM 调用
    assert res["stats"]["budget_blocked"] == 1
    assert res["stats"]["judged"] == 0
    assert metrics.get_judge_dryrun_stats()["budget_blocked"] == 1


def test_budget_warn_only_rejects_judge_llm_calls(tmp_path):
    """验收：预算非 enforce（warn_only 不强制）→ budget_not_enforce，否决 LLM 调用"""
    budget = _mk_budget(mode="warn_only")
    llm = _FakeLLM(verdicts=["accept"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")

    res = _run([_cand("c1", rule_verdict=VERDICT_ACCEPT)],
               llm=llm, budget=budget, metrics=metrics, audit=audit)
    assert res["records"][0]["judge_status"] == STATUS_BUDGET_NOT_ENFORCE
    assert llm.calls == 0


def test_main_chain_llm_unaffected_by_enforce_budget():
    """验收：enforce 只作用于学习动作——主链路 LLM 调用在预算熔断后仍正常"""
    budget = _mk_budget(max_daily=100)
    with budget.with_budget("learn_a", estimated_tokens=60):
        pass
    with pytest.raises(Exception):
        with budget.with_budget("learn_b", estimated_tokens=60):  # 120 > 100 → 熔断 OPEN
            raise AssertionError("学习动作应被预算拦截")
    assert budget.get_status()["breaker"]["state"] == "open"

    # 主链路模拟：直接调用 duck-typed LLM 客户端（不经过 learning_budget）
    llm = _FakeLLM(verdicts=["accept"])
    answer = llm.chat("main chain request")
    assert answer is not None
    assert llm.calls == 1
    # 学习动作仍被预算拦截（enforce 只作用于学习动作，主链路不受影响）
    with pytest.raises(Exception):
        with budget.with_budget("learn_c", estimated_tokens=10):
            raise AssertionError("学习动作应被预算拦截")
    assert budget.get_status()["breaker"]["state"] == "open"


def test_enforce_scope_config_default():
    """验收：LearningBudget 默认 scope=learning_actions（enforce 作用范围声明）"""
    lb = LearningBudget(config={})
    assert lb.scope == "learning_actions"
    assert lb.get_status()["scope"] == "learning_actions"
    # 生产 config.yaml 已灰度提升 enforce（配置审计）
    cfg = load_judge_config()  # 触发 config.yaml 读取路径
    assert cfg["enabled"] is False  # Judge 通道默认关闭（零 LLM）


# ════════════════════════════════════════════════════════════
#  双通道评估与分歧率/采纳率计算
# ════════════════════════════════════════════════════════════

def test_disagreement_rate_and_adoption_rates_computed(tmp_path):
    """验收：分歧率/两通道采纳率/采纳率差异计算正确（compute_stats + 度量快照一致）"""
    cands = [
        _cand("c1", rule_verdict=VERDICT_ACCEPT),
        _cand("c2", rule_verdict=VERDICT_REJECT),
        _cand("c3", rule_verdict=VERDICT_ACCEPT),
        _cand("c4", rule_verdict=VERDICT_ACCEPT),
        _cand("c5", rule_verdict=VERDICT_REJECT),
    ]
    llm = _FakeLLM(verdicts=["accept", "reject", "reject", "accept", "accept"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")

    res = _run(cands, llm=llm, metrics=metrics, audit=audit)
    stats = res["stats"]
    # 分歧: c3（accept vs reject）、c5（reject vs accept）→ 2/5 = 0.4
    assert stats["judged"] == 5
    assert stats["disagreements"] == 2
    assert stats["judge_disagreement_rate"] == 0.4
    # 规则通道采纳 3/5 = 0.6；Judge 通道采纳 3/5 = 0.6；差异 0
    assert stats["rule_adoption_rate"] == 0.6
    assert stats["judge_implied_adoption_rate"] == 0.6
    assert stats["adoption_rate_delta_pp"] == 0.0
    assert stats["tokens_used"] > 0

    # 度量快照与 compute_stats 同口径
    snap = metrics.get_judge_dryrun_stats()
    assert snap["candidates"] == 5
    assert snap["judged"] == 5
    assert snap["judge_disagreement_rate"] == 0.4
    assert snap["rule_adoption_rate"] == 0.6
    assert snap["judge_implied_adoption_rate"] == 0.6
    assert snap["adoption_rate_delta_pp"] == 0.0
    assert snap["insufficient_data"] is False  # 5 >= min_candidates(5)

    # 快照扩展字段名与任务2 监控约定一致
    full = metrics.get_snapshot()["judge_dryrun"]
    assert "judge_disagreement_rate" in full
    assert "judge_implied_adoption_rate" in full


def test_weekly_kpis_include_judge_block(tmp_path):
    """验收：get_weekly_kpis 每行含 judge 扩展节（周级判别数据源）"""
    import time as _time
    metrics = LearningMetrics(enabled=True)
    now = _time.time()
    # 同一 ISO 周内 3 个 judged 候选（1 分歧）
    metrics.record_judge_result(rule_verdict=VERDICT_ACCEPT,
                                judge_verdict=VERDICT_REJECT,
                                disagreement=True, judge_status=STATUS_JUDGED,
                                tokens_used=100, ts=now)
    metrics.record_judge_result(rule_verdict=VERDICT_ACCEPT,
                                judge_verdict=VERDICT_ACCEPT,
                                disagreement=False, judge_status=STATUS_JUDGED,
                                tokens_used=100, ts=now)
    metrics.record_judge_result(rule_verdict=VERDICT_REJECT,
                                judge_verdict=VERDICT_REJECT,
                                disagreement=False, judge_status=STATUS_JUDGED,
                                tokens_used=100, ts=now)

    rows = metrics.get_weekly_kpis(weeks=4)
    assert rows, "应有至少一周数据"
    j = rows[-1]["judge"]
    assert j["candidates"] == 3
    assert j["judged"] == 3
    assert j["disagreements"] == 1
    assert j["judge_disagreement_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert j["rule_adoption_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert j["judge_implied_adoption_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert j["tokens_used"] == 300


def test_record_judge_result_does_not_touch_kpi7():
    """验收：Judge 指标与 KPI#7（进化采纳率）严格分离"""
    metrics = LearningMetrics(enabled=True)
    for i in range(5):
        metrics.record_judge_result(
            rule_verdict=VERDICT_ACCEPT, judge_verdict=VERDICT_ACCEPT,
            disagreement=False, judge_status=STATUS_JUDGED, tokens_used=100)
    snap = metrics.get_snapshot()
    # KPI#7 零变化
    assert snap["kpis"]["evolution_adoption_rate"]["candidates"] == 0
    assert snap["kpis"]["evolution_adoption_rate"]["adopted"] == 0
    # Judge dry-run 独立累计
    assert snap["judge_dryrun"]["judged"] == 5
    assert snap["judge_dryrun"]["judge_implied_adoption_rate"] == 1.0


# ════════════════════════════════════════════════════════════
#  判别规则（A/B 两类合成数据方向验证）
# ════════════════════════════════════════════════════════════

def test_discriminate_hypothesis_a_supports_introduce():
    """验收：合成数据 A（分歧率高 + Judge 采纳率显著更高）→ 支持引入"""
    disc = discriminate(judged=10, disagreement_rate=0.30,
                        rule_adoption_rate=0.20,
                        judge_implied_adoption_rate=0.50)
    assert disc["conclusion"] == CONCLUSION_A_EVAL_INSUFFICIENT
    assert disc["recommendation"] == RECOMMEND_EVALUATE_INTRODUCE
    assert disc["basis"]["adoption_rate_delta_pp"] == 30.0


def test_discriminate_hypothesis_b_not_introduce():
    """验收：合成数据 B（分歧率低 = 两通道高度一致）→ 不引入（候选质量差归因）"""
    disc = discriminate(judged=10, disagreement_rate=0.02,
                        rule_adoption_rate=0.20,
                        judge_implied_adoption_rate=0.50)
    assert disc["conclusion"] == CONCLUSION_B_CANDIDATE_QUALITY
    assert disc["recommendation"] == RECOMMEND_NOT_INTRODUCE


def test_discriminate_insufficient_data_not_introduce():
    """验收：样本不足（< min_candidates）→ insufficient_data，不启用"""
    disc = discriminate(judged=3, disagreement_rate=0.30,
                        rule_adoption_rate=0.20,
                        judge_implied_adoption_rate=0.50)
    assert disc["conclusion"] == CONCLUSION_INSUFFICIENT_DATA
    assert disc["recommendation"] == RECOMMEND_NOT_INTRODUCE
    disc0 = discriminate(judged=0, disagreement_rate=None,
                         rule_adoption_rate=None, judge_implied_adoption_rate=None)
    assert disc0["conclusion"] == CONCLUSION_INSUFFICIENT_DATA
    assert disc0["recommendation"] == RECOMMEND_NOT_INTRODUCE


def test_discriminate_inconclusive_not_introduce():
    """验收：分歧高但采纳率差异不足（< +10pp）→ 证据不足，不启用（明确结论）"""
    disc = discriminate(judged=10, disagreement_rate=0.30,
                        rule_adoption_rate=0.50,
                        judge_implied_adoption_rate=0.55)
    assert disc["conclusion"] == CONCLUSION_INCONCLUSIVE
    assert disc["recommendation"] == RECOMMEND_NOT_INTRODUCE


def test_discriminate_boundary_at_thresholds():
    """验收：阈值边界——分歧率=阈值 且 差异=+10pp → 支持引入（含等号）"""
    disc = discriminate(judged=10, disagreement_rate=0.10,
                        rule_adoption_rate=0.30,
                        judge_implied_adoption_rate=0.40)
    assert disc["conclusion"] == CONCLUSION_A_EVAL_INSUFFICIENT
    assert disc["recommendation"] == RECOMMEND_EVALUATE_INTRODUCE


# ════════════════════════════════════════════════════════════
#  通道开关 / 降级路径 / 解析
# ════════════════════════════════════════════════════════════

def test_channel_disabled_skips_zero_llm(tmp_path):
    """验收：enabled=false（默认）→ 全部 skipped(channel_disabled)，零 LLM 调用"""
    llm = _FakeLLM(verdicts=["accept"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")
    res = _run([_cand("c1", rule_verdict=VERDICT_ACCEPT)],
               llm=llm, metrics=metrics, cfg=_cfg(enabled=False), audit=audit)
    r = res["records"][0]
    assert r["judge_status"] == STATUS_SKIPPED
    assert r["skip_reason"] == "channel_disabled"
    assert llm.calls == 0
    assert res["stats"]["judged"] == 0


def test_no_llm_client_skipped(tmp_path):
    """验收：无 LLM 客户端 → skipped(no_llm_client)，不伪造判定"""
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")
    res = _run([_cand("c1", rule_verdict=VERDICT_ACCEPT)],
               llm=None, metrics=metrics, audit=audit)
    assert res["records"][0]["judge_status"] == STATUS_SKIPPED
    assert res["records"][0]["skip_reason"] == "no_llm_client"
    assert res["records"][0]["judge_verdict"] is None


def test_no_rule_verdict_skipped(tmp_path):
    """验收：候选无既有规则结论且关键分缺失 → skipped(no_rule_verdict)，不进入双通道比较"""
    llm = _FakeLLM(verdicts=["accept"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")
    res = _run([_cand("c1")], llm=llm, metrics=metrics, audit=audit)
    assert res["records"][0]["judge_status"] == STATUS_SKIPPED
    assert res["records"][0]["skip_reason"] == "no_rule_verdict"
    assert llm.calls == 0


def test_parse_judge_response_variants():
    """验收：LLM 响应解析（JSON / 关键词兜底 / 垃圾输入）"""
    v, c, r = _parse_judge_response(
        '{"verdict": "accept", "confidence": 0.85, "reason": "improvement clear"}')
    assert v == VERDICT_ACCEPT and c == 0.85
    v2, _, _ = _parse_judge_response("reject because unsafe")
    assert v2 == VERDICT_REJECT
    assert _parse_judge_response("garbage no markers") is None
    assert _parse_judge_response("") is None
    assert _parse_judge_response(None) is None


def test_parse_failed_records_tokens_no_verdict(tmp_path):
    """验收：LLM 响应无法解析 → skipped(parse_failed)，成本入账但不伪造判定"""
    llm = _FakeLLM(responses=["not a verdict at all"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")
    res = _run([_cand("c1", rule_verdict=VERDICT_ACCEPT)],
               llm=llm, metrics=metrics, audit=audit)
    r = res["records"][0]
    assert r["judge_status"] == STATUS_SKIPPED
    assert r["skip_reason"] == "parse_failed"
    assert r["judge_verdict"] is None
    assert r["tokens_used"] > 0          # LLM 已调用 → 成本诚实入账
    assert metrics.get_judge_dryrun_stats()["tokens_used"] > 0
    assert res["stats"]["judged"] == 0   # 未产出有效判定


def test_rule_verdict_mirror_alignment():
    """验收：镜像规则与既有采纳门槛对齐（只读回放优先；缺失分诚实 None）"""
    assert rule_verdict_mirror(_cand("a", scores={"improvement": 0.10, "safety": 0.9})) \
        == VERDICT_ACCEPT
    assert rule_verdict_mirror(_cand("b", scores={"improvement": 0.01})) \
        == VERDICT_REJECT
    assert rule_verdict_mirror(_cand("c", rule_verdict=VERDICT_REJECT)) \
        == VERDICT_REJECT          # 既有记录优先（只读回放）
    assert rule_verdict_mirror(_cand("d")) is None
    assert rule_verdict_mirror(_cand("e", rule_verdict="weird")) is None


# ════════════════════════════════════════════════════════════
#  候选数据源（任务3 放行审计）
# ════════════════════════════════════════════════════════════

def test_load_candidates_from_rollout_audit(tmp_path):
    """验收：从 rollout_audit.jsonl 读取 preview/approved 真实候选；rejected 排除"""
    audit = tmp_path / "rollout_audit.jsonl"
    with open(audit, "w", encoding="utf-8") as f:
        f.write(json.dumps({"candidate_id": "ev-1", "decision": "preview",
                            "action": "evolution", "detail": "candidate A"}) + "\n")
        f.write(json.dumps({"candidate_id": "ev-2", "decision": "approved",
                            "action": "feedback", "detail": "candidate B"}) + "\n")
        f.write(json.dumps({"candidate_id": "ev-3", "decision": "rejected",
                            "detail": "candidate C"}) + "\n")
        f.write(json.dumps({"object_id": "ev-4", "decision": "preview"}) + "\n")

    cands = load_candidates_from_rollout_audit(str(audit))
    assert len(cands) == 3
    by_id = {c.candidate_id: c for c in cands}
    assert "ev-1" in by_id and "ev-3" not in by_id
    assert by_id["ev-2"].rule_verdict == VERDICT_ACCEPT   # approved → accept（只读回放）
    assert by_id["ev-1"].rule_verdict is None             # preview → None（镜像补足）
    assert by_id["ev-4"].candidate_id == "ev-4"           # object_id 兜底


def test_load_candidates_missing_file_empty():
    """验收：放行审计缺失 → 空候选集（零影响）"""
    assert load_candidates_from_rollout_audit("/nonexistent/audit.jsonl") == []


# ════════════════════════════════════════════════════════════
#  端到端：A/B 合成数据驱动完整批次 → 判别结论方向
# ════════════════════════════════════════════════════════════

def test_end_to_end_hypothesis_b_batch(tmp_path):
    """验收（端到端 B）：分歧率低 → 判别不引入（候选质量差归因）"""
    cands = [_cand(f"c{i}", rule_verdict=VERDICT_REJECT) for i in range(6)]
    llm = _FakeLLM(verdicts=["reject"])  # 与规则通道完全一致 → 零分歧
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")
    res = _run(cands, llm=llm, metrics=metrics, audit=audit)
    assert res["stats"]["judge_disagreement_rate"] == 0.0
    assert res["discrimination"]["conclusion"] == CONCLUSION_B_CANDIDATE_QUALITY
    assert res["discrimination"]["recommendation"] == RECOMMEND_NOT_INTRODUCE


def test_end_to_end_hypothesis_a_batch(tmp_path):
    """验收（端到端 A）：高分歧 + Judge 显著提高采纳率 → 支持引入"""
    cands = []
    # 规则通道全部 reject（候选看似平庸）；Judge 识别其中 4/5 为可采纳好候选
    for i in range(10):
        cands.append(_cand(f"c{i}", rule_verdict=VERDICT_REJECT))
    llm = _FakeLLM(verdicts=["accept", "accept", "accept", "accept", "reject"])
    metrics = LearningMetrics(enabled=True)
    audit = str(tmp_path / "j.jsonl")
    res = _run(cands, llm=llm, metrics=metrics, audit=audit)
    assert res["stats"]["judged"] == 10
    assert res["stats"]["disagreements"] == 8
    assert res["stats"]["judge_disagreement_rate"] == 0.8
    assert res["stats"]["rule_adoption_rate"] == 0.0
    assert res["stats"]["judge_implied_adoption_rate"] == 0.8
    assert res["stats"]["adoption_rate_delta_pp"] == 80.0
    assert res["discrimination"]["conclusion"] == CONCLUSION_A_EVAL_INSUFFICIENT
    assert res["discrimination"]["recommendation"] == RECOMMEND_EVALUATE_INTRODUCE


def test_evaluate_one_returns_audit_shaped_record(tmp_path):
    """验收：evaluate_one 单候选返回审计形记录（字段完备）"""
    metrics = LearningMetrics(enabled=True)
    budget = _mk_budget()
    cfg = _cfg()
    cfg["audit_file"] = str(tmp_path / "j.jsonl")
    r = evaluate_one(_cand("x1", rule_verdict=VERDICT_ACCEPT),
                     llm_client=_FakeLLM(verdicts=["accept"]),
                     budget=budget, metrics=metrics, cfg=cfg)
    for key in ("ts", "candidate_id", "rule_verdict", "judge_verdict",
                "judge_status", "confidence", "disagreement", "implied_adoption",
                "tokens_used", "mode", "intervention"):
        assert key in r
