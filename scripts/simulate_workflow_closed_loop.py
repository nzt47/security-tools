"""模拟对话场景验证工作流自动闭环（学习 → 质量门控 → 置信度收敛 → 稳定拦截 → 降级）

场景设计（8 轮对话）：
  R1  用户: "搜索最新的科技新闻并翻译成英文"（首次，LLM 成功交互，调用 search+translate）
      → 自动学习钩子 learn_from_interaction 落库，conf=0.30
  R2  用户: 再次请求 → 拦截层未命中（matcher min_confidence=0.4 质量门控，新工作流 0 候选）
      → 降级 LLM（防误召回，需先经真实使用验证）
  R3-R5 系统/人工使用工作流 → execute_by_id 模拟执行，conf 收敛 0.18→0.33→0.45（跨过 0.4 门控）
  R6  用户: 再次请求 → 拦截命中（conf=0.451≥0.4, score≈0.41≥0.25）→ 短路跳过 LLM
  R7  用户: 再次请求 → 稳定拦截（score≈0.50）→ skipped_llm=True
  R8  用户: "今天天气怎么样"（无关请求）→ 未命中 → 返回 None → 降级 LLM

数据隔离：临时目录构造 WorkflowLearningService，不污染 data/learned_workflows.json。
工具执行：注入 Mock ToolExecutor（返回假数据），不触达真实外部工具。
验证方式：断言 + 打印完整路径日志（LOG_LEVEL=DEBUG 观察拦截层进入/命中/降级）。

运行: python scripts/simulate_workflow_closed_loop.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.workflow_learning.models import LearningRecord  # noqa: E402
from agent.workflow_learning.service import WorkflowLearningService  # noqa: E402

TASK = "搜索最新的科技新闻并翻译成英文"
UNRELATED = "今天天气怎么样"


def _mock_tool_executor(tool_name: str, params: dict):
    """Mock 工具执行器：返回假数据，不触达真实外部工具"""
    if tool_name == "search":
        return "AI 芯片最新进展：国产大模型加速落地"
    if tool_name == "translate":
        return "Latest AI chip advances: domestic LLMs accelerate deployment"
    return "mock-output"


def _make_learning_record(session_id: str, user_input: str) -> LearningRecord:
    """构造一次成功交互的学习记录（search + translate 调用序列）"""
    return LearningRecord(
        session_id=session_id,
        user_input=user_input,
        tool_calls=[
            {"name": "search", "params": {"query": "最新的科技新闻"},
             "output": "AI 芯片最新进展", "success": True},
            {"name": "translate", "params": {"text": "科技新闻", "target_lang": "en"},
             "output": "Latest AI chip advances", "success": True},
        ],
        success=True,
    )


def _round_conf(conf: float) -> float:
    return round(conf, 3)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "DEBUG"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("simulate_workflow_closed_loop")
    failures: list[str] = []

    # ignore_cleanup_errors: Windows 下 workflow repo 句柄未释放时
    # rmtree 报 NotADirectoryError(267)，清理失败不影响断言结果
    with tempfile.TemporaryDirectory(prefix="wf_closed_loop_",
                                     ignore_cleanup_errors=True) as tmp:
        svc = WorkflowLearningService(
            repo_path=str(tmp),
            min_similarity=0.3,
            min_confidence=0.4,   # matcher 质量门控
            min_score=0.3,        # executor 默认阈值
        )
        svc.set_tool_executor(_mock_tool_executor)

        # ── R1: 学习（自动学习钩子落库）──
        wf = svc.learn_from_interaction(_make_learning_record("sess-001", TASK))
        logger.info("[R1] 学习落库 wf=%s conf=%.3f 步骤=%d", wf.id, wf.confidence, len(wf.steps))
        if _round_conf(wf.confidence) != 0.3:
            failures.append(f"R1 置信度初值应为 0.3, 实际 {wf.confidence}")

        # ── R2: 门控未命中（conf=0.3 < min_confidence=0.4）→ 降级 LLM ──
        r2 = svc.try_execute(TASK, min_score=0.25)
        logger.info("[R2] 拦截层未命中(门控) matched=%s (conf=%.3f<0.4)", r2.matched, wf.confidence)
        if r2.matched:
            failures.append("R2 应未命中（新工作流置信度低于质量门控）")

        # ── R3-R5: 人工/系统使用 execute_by_id，置信度收敛 0.18→0.33→0.45 ──
        confs = []
        for i, _ in enumerate(["R3", "R4", "R5"], start=1):
            res = svc.execute_by_id(wf.id, TASK)
            wf_now = svc.get(wf.id)
            confs.append(round(wf_now.confidence, 2))
            logger.info("[%s] execute_by_id 成功 steps=%d conf=%.2f",
                        ["R3", "R4", "R5"][i-1], res.steps_executed, wf_now.confidence)
        if not res.success:
            failures.append(f"R3-R5 execute_by_id 应全部成功, 最后一个 success={res.success}")
        logger.info("[R3-R5] 置信度收敛轨迹: %s (目标 0.18→0.33→0.45)", confs)

        # ── R6: 拦截命中 → 短路跳过 LLM ──
        r6 = svc.try_execute(TASK, min_score=0.25)
        logger.info("[R6] 拦截命中 matched=%s score=%.3f skipped_llm=%s",
                    r6.matched, getattr(r6, "similarity", 0.0), getattr(r6, "skipped_llm", None))
        if not r6.matched or not r6.success:
            failures.append("R6 应命中并成功执行（置信度已跨过门控）")

        # ── R7: 稳定拦截 ──
        r7 = svc.try_execute(TASK, min_score=0.25)
        logger.info("[R7] 稳定拦截 matched=%s skipped_llm=%s",
                    r7.matched, getattr(r7, "skipped_llm", None))
        if not r7.matched or not getattr(r7, "skipped_llm", False):
            failures.append("R7 应稳定拦截并 skipped_llm=True")

        # ── R8: 无关请求 → 未命中 → 降级 LLM ──
        r8 = svc.try_execute(UNRELATED, min_score=0.25)
        logger.info("[R8] 无关请求未命中 matched=%s", r8.matched)
        if r8.matched:
            failures.append("R8 无关请求不应命中")

    if failures:
        logger.error("❌ 闭环模拟失败 %d 项:\n  - %s", len(failures), "\n  - ".join(failures))
        return 1
    logger.info("✅ 工作流自动闭环 8 轮全部符合预期（学习→门控→收敛→拦截→降级）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
