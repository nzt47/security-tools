"""StagedEvaluator 分阶段评估验证脚本（EVO-T2）

两个场景:
    场景1: 构造低分样本（输出不含期望关键词 + 高延迟）
           → 触发阶段1淘汰（staged.stage1.eliminated），验证 eliminated 分支；
    场景2: 用 data/evals/ 真实任务样本（search/code/chat 三类）
           → 沙盒真实执行 + 分阶段评估（stage1 初筛 → stage2 全量）。

运行方式:
    python scripts/dev/verify_staged_eval.py
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt.evaluator import (
    EvalSamplePool,
    ExecOutcome,
    SkillExecutorEvaluator,
    StagedEvaluator,
)
from agent.skills_mgmt.executor import SkillExecutor, SkillFileStore
from agent.skills_mgmt.models import (
    ContentType,
    Skill,
    SkillCategory,
    SkillMetrics,
    SkillStatus,
)

# 本脚本自包含：不 import run_evolution_demo（该文件为跟踪文件，可能被还原为
# 原始启发式版本导致 _make_skill 无 tags 参数），构造技能依赖评估协议即可。


def _make_skill(skill_id: str, name: str, *,
                usage: int, success_rate: float, avg_latency_ms: float,
                params: Dict[str, Any],
                tags: list[str] | None = None) -> Skill:
    """构造满足候选条件的 Mock Skill（tags 决定 resolve_category 映射）"""
    success_count = int(usage * success_rate)
    return Skill(
        id=skill_id,
        name=name,
        description=f"Mock skill for staged-eval verify: {name}",
        category=SkillCategory.CUSTOM,
        status=SkillStatus.APPROVED,
        enabled=True,
        version="1.0.0",
        content_type=ContentType.MARKDOWN,
        default_params=params,
        tags=tags or [],
        metrics=SkillMetrics(
            usage_count=usage,
            success_count=success_count,
            failure_count=usage - success_count,
            success_rate=success_rate,
            avg_latency_ms=avg_latency_ms,
            p95_latency_ms=avg_latency_ms * 1.5,
            param_stats={},
        ),
    )


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("agent.skills_mgmt.observability").setLevel(logging.INFO)


# ════════════════════════════════════════════════════════════
#  场景1：低分样本 → 阶段1淘汰
# ════════════════════════════════════════════════════════════

def scenario_stage1_eliminated() -> bool:
    print("=" * 70)
    print("  场景1: 构造低分样本 → 触发阶段1淘汰（stage1.eliminated）")
    print("=" * 70)
    # 独立临时样本池：仅含 1 条 search 样本，期望输出必须命中"必须命中"
    tmp = Path(tempfile.mkdtemp(prefix="eval_lowscore_"))
    cat = tmp / "search"
    cat.mkdir(parents=True)
    (cat / "samples.json").write_text(json.dumps([
        {"id": "low-001", "category": "search",
         "task": "查询某个必须命中的关键词",
         "expected_output": {"type": "contains", "values": ["必须命中"]},
         "metadata": {"input": {"query": "低分测试"}}},
    ], ensure_ascii=False), encoding="utf-8")
    pool = EvalSamplePool(base_dir=str(tmp))
    print(f"    低分样本池: {tmp}/search/samples.json（1 条，contains 期望『必须命中』）")

    def bad_runner(skill: Any, params: Dict[str, Any]) -> ExecOutcome:
        # 输出不含期望关键词 + 较高延迟（3000ms）→ contains 失败 + latency_norm 低 → 低分
        return ExecOutcome(success=True, result={"answer": "未命中任何期望内容"},
                           duration_ms=3000.0)

    staged = StagedEvaluator(SkillExecutorEvaluator(pool=pool, runner=bad_runner))
    skill = _make_skill("demo-low-score", "低分技能", usage=20, success_rate=0.5,
                        avg_latency_ms=3000, params={}, tags=["search"])
    print(f"    分阶段参数: stage1_ratio={staged.stage1_ratio}, "
          f"stage1_min_score={staged.stage1_min_score}, "
          f"stage1_max_samples={staged.stage1_max_samples}")
    print("-" * 70)

    r = staged.evaluate(skill)

    print("-" * 70)
    ok = r.eliminated and r.stage == "stage1" and r.score < staged.stage1_min_score
    print(f"    结果: status={r.status} stage={r.stage!r} eliminated={r.eliminated} "
          f"score={r.score:.4f} (阈值 {staged.stage1_min_score}) "
          f"success_rate={r.success_rate:.2f} samples={r.sample_count}")
    print(f"    验证: {'PASS' if ok else 'FAIL'} "
          f"→ {'淘汰分支正常（未进入阶段2）' if ok else '淘汰分支异常'}")
    return ok


# ════════════════════════════════════════════════════════════
#  场景3：预算耗尽 → 阶段1熔断（budget.break / stage1.abort）
# ════════════════════════════════════════════════════════════

def scenario_budget_break() -> bool:
    print()
    print("=" * 70)
    print("  场景3: 预算耗尽 → 阶段1熔断（budget.break / eval.budget_exceeded / stage1.abort）")
    print("=" * 70)
    tmp = Path(tempfile.mkdtemp(prefix="eval_budget_"))
    cat = tmp / "search"
    cat.mkdir(parents=True)
    (cat / "samples.json").write_text(json.dumps([
        {"id": "b-00%d" % i, "category": "search",
         "task": "查询第 %d 号关键词" % i,
         "expected_output": {"type": "contains", "values": ["关键词"]},
         "metadata": {"input": {"query": "关键词 %d" % i}}}
        for i in range(1, 4)
    ], ensure_ascii=False), encoding="utf-8")
    pool = EvalSamplePool(base_dir=str(tmp))

    def fast_runner(skill: Any, params: Dict[str, Any]) -> ExecOutcome:
        return ExecOutcome(success=True, result={"answer": "命中关键词"},
                           duration_ms=1.0)

    # stage1 预算=1：第一条样本的输入估算（≥8）即超限 → 熔断
    staged = StagedEvaluator(
        SkillExecutorEvaluator(pool=pool, runner=fast_runner),
        stage1_budget_tokens=1, stage2_budget_tokens=1)
    skill = _make_skill("demo-budget-break", "预算熔断技能", usage=20,
                        success_rate=0.5, avg_latency_ms=100,
                        params={}, tags=["search"])
    print(f"    样本: 3 条 search；stage1_budget_tokens=1（极小预算强制熔断）")
    print("-" * 70)

    r = staged.evaluate(skill)

    print("-" * 70)
    ok = r.status == "budget_exceeded" and r.stage == "stage1" and r.budget_exceeded
    print(f"    结果: status={r.status} stage={r.stage!r} budget_exceeded={r.budget_exceeded} "
          f"used_tokens={r.cost_tokens} samples={r.sample_count}")
    print(f"    验证: {'PASS' if ok else 'FAIL'} "
          f"→ {'阶段1预算熔断正常（未进入阶段2）' if ok else '熔断异常'}")
    return ok


# ════════════════════════════════════════════════════════════
#  场景4：样本不足 → no_samples（staged.no_samples / eval.no_samples）
# ════════════════════════════════════════════════════════════

def scenario_no_samples() -> bool:
    print()
    print("=" * 70)
    print("  场景4: 样本不足 → no_samples（staged.no_samples / eval.no_samples）")
    print("=" * 70)
    empty = Path(tempfile.mkdtemp(prefix="eval_empty_"))  # 空样本池根目录
    pool = EvalSamplePool(base_dir=str(empty))
    staged = StagedEvaluator(SkillExecutorEvaluator(pool=pool))
    skill = _make_skill("demo-no-samples", "无样本技能", usage=20,
                        success_rate=0.5, avg_latency_ms=100,
                        params={}, tags=["search"])
    print(f"    样本池: {empty}（空目录，无任何类别样本）")
    print("-" * 70)

    r = staged.evaluate(skill)

    print("-" * 70)
    ok = r.status == "no_samples" and r.sample_count == 0
    print(f"    结果: status={r.status} stage={r.stage!r} score={r.score:.4f} "
          f"samples={r.sample_count}（绝不伪造指标）")
    print(f"    验证: {'PASS' if ok else 'FAIL'} → {'no_samples 分支正常' if ok else '异常'}")
    return ok


# ════════════════════════════════════════════════════════════
#  场景5：data/evals 样本格式校验（符合 EvaluationResult 数据结构）
# ════════════════════════════════════════════════════════════

_VALID_CHECK_TYPES = {"contains", "json", "validator", "self_consistency", "exact"}


def scenario_sample_schema() -> bool:
    print()
    print("=" * 70)
    print("  场景5: data/evals 样本格式校验（对齐 EvalSample / EvaluationResult）")
    print("=" * 70)
    pool = EvalSamplePool()
    print(f"    样本池根目录: {pool.base_dir}")
    total = bad = 0
    for c in sorted(pool.categories()):
        samples = pool.load_category(c)
        print(f"    - {c}: {len(samples)} 条")
        for s in samples:
            total += 1
            et = (s.expected_output or {}).get("type") \
                if isinstance(s.expected_output, dict) else None
            if c == "chat":
                # 开放域样本无 expected_output 为合法格式（走自一致性）
                ok = bool(s.id) and bool(s.task) and s.category == c \
                    and (not s.expected_output or et in _VALID_CHECK_TYPES)
            else:
                ok = bool(s.id) and bool(s.task) and s.category == c \
                    and bool(s.expected_output) and et in _VALID_CHECK_TYPES
            if not ok:
                bad += 1
                print(f"      BAD {s.id}: category={s.category} "
                      f"expected_type={et!r}")
    print(f"    合计 {total} 条；合法 {total - bad}，非法 {bad}")
    ok = bad == 0 and total >= 15  # 三类各 5 条
    print(f"    验证: {'PASS' if ok else 'FAIL'} "
          f"→ {'三类样本已正确生成，格式符合数据结构' if ok else '存在格式问题'}")
    return ok


# ════════════════════════════════════════════════════════════
#  场景2：data/evals 真实样本全量评估
# ════════════════════════════════════════════════════════════

# 三类样本专用的沙盒脚本：
#   - code 类: validator 校验裸值（int/bool/str），返回计算结果
#   - search 类: 回显查询关键词 / 结构化 json
#   - chat 类: 开放域固定友好回复 → 自一致性高
_SCRIPT_TEMPLATE = '''\
"""{skill_id} 真实评估沙盒脚本 — data/evals 样本专用"""
import sys
import json


def _fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _run(p):
    task = str(p.get("task", ""))
    # code 类: 按 metadata.input 真实计算（validator 校验裸值）
    if "实现一个函数" in task:
        if p.get("n") is not None:
            if "斐波那契" in task:
                return _fib(int(p["n"]))
            if "和" in task:
                n = int(p["n"])
                return n * (n + 1) // 2
        if p.get("text") is not None:
            text = str(p["text"])
            if "回文" in task:
                return text == text[::-1]
            if "大写" in task:
                return text.upper()
        if p.get("items") is not None:
            return len(set(p["items"]))
        return None
    # search 类: 结构化回显查询关键词
    q = str(p.get("query", ""))
    if p.get("require_json"):
        return {{"found": True, "query": q}}
    if q:
        return {{"answer": "查询结果: " + q, "query": q}}
    # chat 类: 开放域固定友好回复 → 自一致性高
    return {{"reply": "你好！我可以帮你查询信息、处理任务、提供建议。有什么需要帮助的吗？"}}


def main():
    p = json.loads(sys.stdin.read() or "{{}}")
    print(json.dumps(_run(p), ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


def scenario_real_samples() -> bool:
    print()
    print("=" * 70)
    print("  场景2: data/evals/ 真实任务样本全量评估（search/code/chat）")
    print("=" * 70)
    pool = EvalSamplePool()  # data/evals 真实样本池
    specs = [
        ("demo-search-real", "真实搜索技能", "search"),
        ("demo-code-real", "真实代码技能", "code"),
        ("demo-chat-real", "真实对话技能", "chat"),
    ]
    # 为每个技能生成沙盒脚本
    repo = Path(tempfile.mkdtemp(prefix="eval_real_repo_"))
    for sid, _name, _cat in specs:
        script_dir = repo / sid / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "main.py").write_text(
            _SCRIPT_TEMPLATE.format(skill_id=sid), encoding="utf-8")
    print(f"    沙盒仓库: {repo}（每技能含 scripts/main.py）")
    print(f"    样本池:   {pool.base_dir}（真实样本）")
    print("-" * 70)

    executor = SkillExecutor(SkillFileStore(repo_path=str(repo)))

    def runner(skill: Any, params: Dict[str, Any]) -> Any:
        return executor.execute(skill.id, "main.py", params=params, timeout=15)

    all_ok = True
    for sid, name, cat in specs:
        skill = _make_skill(sid, name, usage=30, success_rate=0.7,
                            avg_latency_ms=200, params={}, tags=[cat])
        staged = StagedEvaluator(SkillExecutorEvaluator(
            pool=pool, runner=runner, allow_validator=(cat == "code")))
        print(f"\n    --- {sid} (category={cat}) ---")
        r = staged.evaluate(skill)
        print(f"    status={r.status} stage={r.stage!r} eliminated={r.eliminated} "
              f"score={r.score:.4f} success_rate={r.success_rate:.2f} "
              f"latency={r.latency_ms:.0f}ms samples={r.sample_count} "
              f"used_tokens={r.cost_tokens}")
        for s in r.samples:
            err = f" err={s.error[:60]}" if s.error else ""
            print(f"      - {s.sample_id}: success={s.success} "
                  f"checked_by={s.checked_by} score={s.score:.2f} "
                  f"latency={s.latency_ms:.0f}ms{err}")
        # 全部真实样本应判定成功（脚本正确实现）→ 不允许 skipped/错误
        if r.status == "completed" and r.success_rate > 0.9:
            print(f"    验证: PASS（{cat} 类全量评估完成）")
        else:
            all_ok = False
            print(f"    验证: FAIL（{cat} 类评估异常）")
    return all_ok


def main() -> int:
    setup_logging()
    ok1 = scenario_stage1_eliminated()
    ok3 = scenario_budget_break()
    ok4 = scenario_no_samples()
    ok5 = scenario_sample_schema()
    ok2 = scenario_real_samples()
    print()
    print("=" * 70)
    print(f"  汇总: 淘汰分支={'PASS' if ok1 else 'FAIL'}  "
          f"预算熔断={'PASS' if ok3 else 'FAIL'}  "
          f"no_samples={'PASS' if ok4 else 'FAIL'}  "
          f"样本格式={'PASS' if ok5 else 'FAIL'}  "
          f"真实样本={'PASS' if ok2 else 'FAIL'}")
    print("=" * 70)
    return 0 if (ok1 and ok2 and ok3 and ok4 and ok5) else 1


if __name__ == "__main__":
    sys.exit(main())
