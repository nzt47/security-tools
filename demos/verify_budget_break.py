"""预算耗尽（熔断）机制验证脚本 — EVO-T3 进化循环强化

【验证目标】
    1. 熔断是否"立即停止后续评估": 用带调用计数的评估器精确断言 evaluate
       调用次数 —— 熔断后不再产生任何 evaluate 调用（而不是继续评估剩余变异体）
    2. 熔断轮次是否记录 skipped 日志 + skipped 谱系记录
       （decision=skipped, cost.tokens=熔断前累计，含基线评估）
    3. 附带展示 score_child_prop 父代权重明细日志
       （weight = sigmoid(score) × child_penalty(children)，子代惩罚生效可观测）

【场景设计】
    CountingEvaluator 固定每次评估 cost_tokens=30:
        params=None   （基线评估）→ score≈0.60（低）
        params=dict   （变异体评估）→ score≈0.945（高）
    变异体提升显著 → 正常轮次会提交 → 累积 committed 父代候选与子代数，
    供父代选择权重明细展示。

    场景 0/1: 正常预算（不熔断）跑 5 轮 → 产生 5 个 committed 父代候选，
              第 6 轮选择时打印 score_child_prop 权重明细（children 0~4 的
              penalty 衰减差异可见）
    场景 A:   max_tokens_per_round=30（=基线 cost）→ 熔断于首个变异体评估前
              → 0 个变异体通过评估 → best=None → 走 skipped 路径
              断言: base_calls=1, variant_calls=0, budget_breached=True,
                    decision=skipped, 谱系 cost.tokens=30
    场景 B:   max_tokens_per_round=60 → 熔断于第 2 个变异体评估前
              断言: variant_calls=1（剩余变异体立即停止，不再评估）
                    budget_breached=True（已评估的变异体仍可参与提交判定，
                    这是设计语义: 熔断只中止剩余评估，不丢弃已有结果）

【运行方式】（须在**仓库根**执行）
    python demos/verify_budget_break.py
"""
from __future__ import annotations

import io
import logging
import sys
import tempfile
from pathlib import Path
from typing import Optional

# 【demos/ 迁移 2026-09-21】原在仓库根时 `Path(__file__).parent` 同时兼顾两件事：
#   ① 同目录配对导入 `from run_evolution_demo import ...`（demos/ 自身）；
#   ② `import agent.*`（当时该目录就是仓库根）。
# 迁入 demos/ 后两件事分离，故**两条都保留**：先加 demos/，再加仓库根。
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skills_mgmt.evaluator import EvaluationResult
from agent.skills_mgmt.lineage import EvolutionArchive, print_lineage
from agent.skills_mgmt.observability import logger as obs_logger
from agent.skills_mgmt.offline_evolver import OfflineEvolver
from run_evolution_demo import (
    build_mock_skills, MockStore, MockEnhancer, setup_logging,
)

SKILL = "demo-search-optimize"
BASE_COST = 30          # CountingEvaluator 每次评估固定 token 成本


class CountingEvaluator:
    """固定成本的计数评估器：分别统计基线/变异体的 evaluate 调用次数

    成本固定（不随样本增长）→ 预算耗尽时机精确可控、可复现；
    计数精确断言"熔断后是否还有后续 evaluate 调用"。
    """

    def __init__(self, cost_tokens: int = BASE_COST):
        self.cost_tokens = cost_tokens
        self.base_calls = 0
        self.variant_calls = 0

    @property
    def total_calls(self) -> int:
        return self.base_calls + self.variant_calls

    def evaluate(self, skill, params: Optional[dict] = None) -> EvaluationResult:
        is_variant = params is not None
        if is_variant:
            self.variant_calls += 1
            sr, lat, sat = 0.95, 100.0, 0.9     # 变异体提升显著 → 提交
        else:
            self.base_calls += 1
            sr, lat, sat = 0.50, 500.0, 0.5     # 基线偏低 → 变异体有提升空间
        return EvaluationResult(
            skill_id=skill.id,
            status="completed",
            success_rate=sr,
            latency_ms=lat,
            satisfaction=sat,
            cost_tokens=self.cost_tokens,
            sample_count=3,
            stage="stage2",
            eliminated=False,
            samples=[],
        )


def make_evolver(store, enhancer, archive, *, max_tokens: int):
    """构造 OfflineEvolver（固定 seed，显式注入计数评估器，预算可配）"""
    return OfflineEvolver(
        store, enhancer,
        min_usage=10,
        improvement_threshold=0.01,
        random_seed=42,
        archive=archive,
        max_tokens_per_round=max_tokens,
    )


def run_normal_round(store, enhancer, archive) -> CountingEvaluator:
    """正常预算跑一轮（提交/拒绝均正常），返回该轮计数评估器"""
    ev = CountingEvaluator()
    evolver = make_evolver(store, enhancer, archive, max_tokens=10**9)
    result = evolver.evolve_once(SKILL, trigger="manual", evaluator=ev)
    print(f"    轮次结果: decision={result.decision} "
          f"improvement={result.improvement} "
          f"evaluate_calls(base+variant)={ev.base_calls}+{ev.variant_calls}")
    return ev


def main():
    setup_logging()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 终端不支持时保持默认
            pass

    # 日志捕获（附加 handler，不影响 stdout 正常输出）
    log_buf = io.StringIO()
    log_handler = logging.StreamHandler(log_buf)
    obs_logger.addHandler(log_handler)

    print("=" * 70)
    print("  预算耗尽（熔断）机制验证 — EVO-T3")
    print("  目标: ① 熔断立即停止后续评估 ② 熔断轮次记录 skipped 日志+谱系")
    print("        ③ 展示 score_child_prop 父代权重明细（子代惩罚生效）")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="evo_budget_break_") as tmp:
        archive = EvolutionArchive(
            active_path=str(Path(tmp) / "archive.jsonl"),
            archive_path=str(Path(tmp) / "archive_old.jsonl"),
        )
        store = MockStore(build_mock_skills())
        enhancer = MockEnhancer(lineage_archive=archive)
        print(f"\n[0] 临时档案库: {Path(tmp) / 'archive.jsonl'}")

        # ── 场景0/1: 正常进化累积父代候选，展示权重明细 ──
        print("\n[1] 正常预算跑 5 轮，累积 committed 父代候选...")
        for i in range(5):
            run_normal_round(store, enhancer, archive)
        cands = archive.list_by_object(SKILL)
        committed = [r for r in cands if r.decision == "committed"]
        print(f"    committed 父代候选: {len(committed)} 条")
        assert len(committed) >= 4, "正常轮次应累积多个 committed 父代"

        print("\n[2] 第 6 轮触发父代选择 → score_child_prop 权重明细日志")
        print("    （观察 children → child_penalty 衰减 → weight 变化）")
        run_normal_round(store, enhancer, archive)

        # ── 场景 A: 预算=基线 cost → 首个变异体评估前熔断 → skipped ──
        print(f"\n[3] 场景A: max_tokens_per_round={BASE_COST} "
              f"(=基线成本) → 熔断于首个变异体评估前")
        ev_a = CountingEvaluator()
        evolver_a = make_evolver(store, enhancer, archive,
                                 max_tokens=BASE_COST)
        result_a = evolver_a.evolve_once(SKILL, trigger="scheduler",
                                         evaluator=ev_a)
        print(f"    decision={result_a.decision} "
              f"budget_breached={result_a.budget_breached} "
              f"cost_tokens={result_a.cost_tokens}")
        print(f"    evaluate 调用: base={ev_a.base_calls} "
              f"variant={ev_a.variant_calls} (0 个变异体被评估 → 立即停止)")
        assert ev_a.base_calls == 1, "基线评估应恰好 1 次"
        assert ev_a.variant_calls == 0, "熔断后不得评估任何变异体"
        assert result_a.budget_breached, "场景A 应触发预算熔断"
        assert result_a.decision == "skipped", "无有效变异体 → skipped"
        assert result_a.cost_tokens == BASE_COST, \
            "熔断轮 cost 应为熔断前累计（含基线评估）"
        skipped_a = [r for r in archive.list_by_object(SKILL)
                     if r.decision == "skipped"]
        latest_a = skipped_a[-1] if skipped_a else None
        assert latest_a is not None, "熔断轮次应写 skipped 谱系记录"
        assert latest_a.decision_reason == "无有效变异体通过评估", \
            f"skipped 原因不符: {latest_a.decision_reason}"
        assert latest_a.cost and latest_a.cost.get("tokens") == BASE_COST, \
            f"谱系 cost.tokens 应为 {BASE_COST}: {latest_a.cost}"
        print(f"    谱系 skipped 记录: reason={latest_a.decision_reason} "
              f"cost.tokens={latest_a.cost.get('tokens')} ✓")

        # ── 场景 B: 预算=基线+1×变异体 → 第 2 个变异体前熔断 → 立即停止 ──
        print(f"\n[4] 场景B: max_tokens_per_round={BASE_COST * 2} "
              f"→ 评估 1 个变异体后熔断，剩余立即停止")
        ev_b = CountingEvaluator()
        evolver_b = make_evolver(store, enhancer, archive,
                                 max_tokens=BASE_COST * 2)
        result_b = evolver_b.evolve_once(SKILL, trigger="scheduler",
                                         evaluator=ev_b)
        print(f"    decision={result_b.decision} "
              f"budget_breached={result_b.budget_breached}")
        print(f"    evaluate 调用: base={ev_b.base_calls} "
              f"variant={ev_b.variant_calls} "
              f"(共 6 个变异体，仅 1 个被评估 → 剩余 5 个立即停止)")
        assert ev_b.base_calls == 1, "基线评估应恰好 1 次"
        assert ev_b.variant_calls == 1, "预算只够评估 1 个变异体即熔断"
        assert result_b.budget_breached, "场景B 应触发预算熔断"
        # 设计语义: 熔断只中止剩余评估；已评估的变异体仍参与提交判定
        print("    说明: 熔断只中止剩余评估，已评估的变异体仍参与提交判定")

        # ── 日志断言: budget_break + skipped 均已记录 ──
        log_handler.flush()
        logs = log_buf.getvalue()
        print("\n[5] 日志断言...")
        assert "evolve_once.budget_break" in logs, "缺少 budget_break 熔断日志"
        assert "evolve_once.skipped" in logs, "缺少 evolve_once.skipped 日志"
        assert "权重明细(weight=sigmoid(score)*child_penalty(children))" in logs, \
            "缺少 score_child_prop 权重明细日志"
        print("    evolve_once.budget_break  ✓ (熔断告警)")
        print("    evolve_once.skipped       ✓ (熔断轮次记录)")
        print("    ParentSelection 权重明细  ✓ (子代惩罚可观测)")

        # ── 输出关键日志（供人工核对）──
        print("\n[6] 关键日志摘录（budget_break / skipped / 权重明细）")
        for line in logs.splitlines():
            if any(k in line for k in (
                    "evolve_once.budget_break",
                    "evolve_once.skipped",
                    "ParentSelection] skill=")):
                print("    " + line[:240])

        print("\n" + "=" * 70)
        print("  预算熔断机制验证通过 ✓")
        print("  ① 熔断立即停止后续评估（evaluate 调用计数精确停止）")
        print("  ② 熔断轮次记录 skipped 日志 + 谱系（cost.tokens=熔断前累计）")
        print("  ③ score_child_prop 子代惩罚权重明细可观测")
        print("=" * 70)

    obs_logger.removeHandler(log_handler)


if __name__ == "__main__":
    main()
