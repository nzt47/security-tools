"""chat 类 stage2 自一致性耗时瓶颈分析（EVO-T2 性能专项）

测量内容（复用 data/evals/chat 真实样本 + 真实沙盒 runner）:
    1. 单样本 3 次自一致性执行的总耗时与逐次拆分
    2. 子进程沙盒启动开销（Python 解释器冷启动）vs 脚本执行本体
    3. 自一致性 Jaccard 相似度计算耗时
    4. 反馈信号查询耗时
    5. search（有 expected_output，单次执行）对照

运行方式:
    python scripts/dev/analyze_chat_stage2.py
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt.evaluator import (
    EvalSamplePool,
    ExecOutcome,
    FeedbackSignalScorer,
    SkillExecutorEvaluator,
)
from agent.skills_mgmt.executor import SkillExecutor, SkillFileStore
from agent.skills_mgmt.models import (
    ContentType,
    Skill,
    SkillCategory,
    SkillMetrics,
    SkillStatus,
)


# 三类样本专用沙盒脚本（与 verify_staged_eval 场景2 对齐）
_SCRIPT_TEMPLATE = '''\
"""{skill_id} 真实评估沙盒脚本 — 自一致性耗时分析专用"""
import sys
import json


def _fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _run(p):
    task = str(p.get("task", ""))
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
    q = str(p.get("query", ""))
    if p.get("require_json"):
        return {{"found": True, "query": q}}
    if q:
        return {{"answer": "查询结果: " + q, "query": q}}
    return {{"reply": "你好！我可以帮你查询信息、处理任务、提供建议。有什么需要帮助的吗？"}}
'''


def _make_skill(skill_id: str, tags: list[str]) -> Skill:
    return Skill(
        id=skill_id, name=skill_id, description=f"analyze: {skill_id}",
        category=SkillCategory.CUSTOM, status=SkillStatus.APPROVED,
        enabled=True, version="1.0.0",
        content_type=ContentType.MARKDOWN,
        default_params={}, tags=tags,
        metrics=SkillMetrics(usage_count=1, success_count=1,
                             failure_count=0, success_rate=1.0,
                             avg_latency_ms=0.0, p95_latency_ms=0.0,
                             param_stats={}),
    )


def _measure_ms(fn) -> float:
    t = time.monotonic()
    fn()
    return (time.monotonic() - t) * 1000


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)
    logging.getLogger("agent.skills_mgmt.observability").setLevel(logging.WARNING)

    pool = EvalSamplePool()  # 默认 data/evals
    print("=" * 72)
    print("  chat 类 stage2 自一致性耗时瓶颈分析（EVO-T2 性能专项）")
    print("=" * 72)
    print(f"样本池: {pool.base_dir}")
    print(f"  search: {len(pool.get('search'))} 条 | code: {len(pool.get('code'))} 条 "
          f"| chat: {len(pool.get('chat'))} 条")
    print(f"consistency_runs 默认 = 3（EVAL_CONSISTENCY_RUNS）→ chat 每样本执行 3 次")

    # ── 真实沙盒 runner：临时仓库 + 每技能 main.py ──
    repo = Path(tempfile.mkdtemp(prefix="eval_analyze_repo_"))
    for sid in ("analyze-chat", "analyze-search"):
        script_dir = repo / sid / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "main.py").write_text(
            _SCRIPT_TEMPLATE.format(skill_id=sid), encoding="utf-8")
    executor = SkillExecutor(SkillFileStore(repo_path=str(repo)))

    def real_runner(skill: Any, params: Dict[str, Any]) -> Any:
        return executor.execute(skill.id, "main.py", params=params, timeout=15)

    chat_skill = _make_skill("analyze-chat", ["chat"])
    search_skill = _make_skill("analyze-search", ["search"])

    # ── 对照1：search 单样本（有 expected_output，仅 1 次执行） ──
    ev_search = SkillExecutorEvaluator(pool=pool, runner=real_runner,
                                       use_feedback=False, allow_validator=True)
    s = pool.get("search")[0]
    r, ms = ev_search.evaluate(search_skill, sample_ids=[s.id]), 0.0
    t = time.monotonic()
    r = ev_search.evaluate(search_skill, sample_ids=[s.id])
    ms = (time.monotonic() - t) * 1000
    print(f"\n对照1: search 单样本（expected_output 精确判定, 1 次执行）")
    print(f"  evaluate() 总耗时 = {ms:8.1f} ms | success={r.success_rate:.2f} "
          f"checked_by={r.samples[0].checked_by}")

    # ── chat 单样本自一致性逐环节计时（真实沙盒） ──
    sample = pool.get("chat")[0]
    params = dict(sample.metadata.get("input") or {})
    params["task"] = sample.task
    params["sample_id"] = sample.id
    ev_chat = SkillExecutorEvaluator(pool=pool, runner=real_runner,
                                     use_feedback=False, consistency_runs=3)

    print(f"\n── chat 单样本自一致性拆解（真实沙盒）──")
    print(f"  样本: {sample.id} | expected_output={sample.expected_output!r} "
          f"→ 走自一致性")
    runs = []
    for i in range(3):
        pid = params["sample_id"] if i == 0 else f"{sample.id}#{i}"
        t = time.monotonic()
        out = ev_chat._run(chat_skill, dict(params, sample_id=pid))
        ms = (time.monotonic() - t) * 1000
        runs.append((out, ms))
        print(f"  {i+1}. 沙盒执行(第{i+1}次): {ms:8.1f} ms "
              f"success={out.success} duration_ms={out.duration_ms}")

    outputs = [r.result for r, _ in runs if r.success]
    t = time.monotonic()
    score = ev_chat._scorer.score(outputs)
    ms_score = (time.monotonic() - t) * 1000
    print(f"  4. Jaccard 相似度计算:   {ms_score:8.1f} ms (score={score:.4f})")

    ms_exec_total = sum(m for _, m in runs)
    print(f"  ── 自一致性路径合计: {ms_exec_total + ms_score:8.1f} ms "
          f"（沙盒执行 {ms_exec_total:.0f} + 相似度 {ms_score:.0f}）")

    # ── 反馈查询计时 ──
    t = time.monotonic()
    fs = FeedbackSignalScorer().satisfaction(chat_skill.id)
    ms_fb = (time.monotonic() - t) * 1000
    print(f"  5. 反馈信号查询:       {ms_fb:8.1f} ms (satisfaction={fs})")

    # ── 对照2：dry runner（纯 Python 侧, 无子进程） ──
    def dry_runner(skill: Any, params: Dict[str, Any]) -> ExecOutcome:
        task = str(params.get("task", ""))
        result = {"reply": "你好！我可以帮你查询信息、处理任务、提供建议。"}
        return ExecOutcome(success=True, result=result, duration_ms=0.1)

    ev_dry = SkillExecutorEvaluator(pool=pool, runner=dry_runner,
                                    use_feedback=False, consistency_runs=3)
    t = time.monotonic()
    ev_dry.evaluate(chat_skill)
    ms_dry_total = (time.monotonic() - t) * 1000
    print(f"\n对照2: chat 全样本(5条×3次=15次) dry runner 总耗时 = "
          f"{ms_dry_total:8.1f} ms（纯 Python 侧, 无子进程）")

    # ── 结论 ──
    print("\n" + "=" * 72)
    print("  结论：自一致性把每样本执行次数从 1 提升到 3（×3），")
    print("        每次执行都是独立子进程冷启动 → 子进程开销为主导成本。")
    print("=" * 72)


if __name__ == "__main__":
    main()
