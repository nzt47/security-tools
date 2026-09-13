"""模拟对话场景验证工作流自动闭环（学习 → 门控 → 置信度收敛 → 稳定拦截 → 降级）

场景设计：
  R1  用户: "搜索最新的 AI 科技新闻并翻译成英文"（首次，LLM 成功交互，调用 search+translate）
      → 自动学习钩子 learn_from_interaction 落库，conf=0.40（冷启动初值 = matcher 门槛）
  R2  用户: 再次请求 → 冷启动即可拦截（conf=0.40 ≥ min_confidence=0.40）
      → matched=True、skipped_llm=True（冷启动死锁修复后的既定语义）
  R3-R5 系统/人工使用工作流 → execute_by_id 模拟执行，conf 单调上升（0.40→0.50→0.58→0.65）
  R6  用户: 再次请求 → 拦截命中（score ≈ 0.7+ ≥ min_score=0.25）→ 短路跳过 LLM
  R7  用户: 再次请求 → 稳定拦截 → skipped_llm=True
  R8  用户: "今天天气怎么样"（无关请求）→ 未命中 → 降级 LLM

  R9（TASK-S10-01 新增）单轮单工具调用 + 中文单字触发词 → **准入门槛**拦下：
      草稿态、不进匹配候选（不拦截）、不进自动升格候选、force 转技能被拒

数据隔离：临时目录构造 WorkflowLearningService，不污染 data/learned_workflows.json。
工具执行：注入 Mock ToolExecutor（返回假数据），不触达真实外部工具。
验证方式：断言 + 打印完整路径日志（LOG_LEVEL=DEBUG 观察拦截层进入/命中/降级）。

--- TASK-S10-01 说明（为什么改了本脚本的期望值）---
本脚本在修改前**已是红的**（基线实测 2 项失败）：
    - R1 期望 conf=0.30，而 learner 早已改为冷启动初值 0.40；
    - R2 期望"新工作流置信度低于质量门控 → 未命中"，而"冷启动死锁修复"
      之后初值即等于门槛、可立即命中。
两处都是脚本期望值滞后于代码（注释里记录了改动理由），不是代码缺陷。
本次随 TASK-S10-01 一并按**当前语义**重新基线，并补 R9 断言新准入门槛。
本次未放宽任何断言：R1/R2/R6/R7/R8 仍为严格相等/布尔断言，R9 为新增严格断言。

运行: python scripts/simulate_workflow_closed_loop.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.workflow_learning.admission import (  # noqa: E402
    CODE_NO_DISCRIMINATIVE_TRIGGER, CODE_STEPS_TOO_FEW,
)
from agent.workflow_learning.models import (  # noqa: E402
    LearningRecord, WorkflowStatus,
)
from agent.workflow_learning.service import WorkflowLearningService  # noqa: E402
from agent.workflow_learning.skill_converter import (  # noqa: E402
    WorkflowConvertError, WorkflowToSkillConverter,
)

TASK = "搜索最新的 AI 科技新闻并翻译成英文"
UNRELATED = "今天天气怎么样"
#: 单轮单工具调用 + 中文（按字切分 → 全是单字触发词）→ 结构性不可准入
DIRTY_TASK = "帮我列出当前工作目录下的文件"
LEARN_CONF_INIT = 0.4   # learner 冷启动初值 = matcher.min_confidence


def _mock_tool_executor(tool_name: str, params: dict):
    """Mock 工具执行器：返回假数据，不触达真实外部工具"""
    if tool_name == "search":
        return "AI 芯片最新进展：国产大模型加速落地"
    if tool_name == "translate":
        return "Latest AI chip advances: domestic LLMs accelerate deployment"
    if tool_name == "list_directory":
        return {"ok": True, "path": ".", "entries": ["a.txt", "b.py"]}
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


def _make_dirty_record(session_id: str) -> LearningRecord:
    """单轮单工具调用（1 步 → 不是可复用工作流）"""
    return LearningRecord(
        session_id=session_id,
        user_input=DIRTY_TASK,
        tool_calls=[
            {"name": "list_directory", "params": {"path": "."},
             "output": {"ok": True}, "success": True},
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
        logger.info("[R1] 学习落库 wf=%s conf=%.3f 步骤=%d 触发词=%s",
                    wf.id, wf.confidence, len(wf.steps), wf.trigger_patterns)
        if len(wf.steps) != 2:
            failures.append(f"R1 应学到 2 步，实际 {len(wf.steps)}")
        if wf.trigger_patterns != ["ai"]:
            failures.append(
                f"R1 触发词应只保留有区分度的 ['ai']，实际 {wf.trigger_patterns}")
        if wf.status != WorkflowStatus.ACTIVE.value:
            failures.append(f"R1 达标工作流应为 active，实际 {wf.status}")
        if _round_conf(wf.confidence) != LEARN_CONF_INIT:
            failures.append(f"R1 冷启动置信度应为 {LEARN_CONF_INIT}, "
                            f"实际 {wf.confidence}")

        # ── R2: 冷启动即可拦截（conf 初值 = 门槛；冷启动死锁修复语义）──
        r2 = svc.try_execute(TASK, min_score=0.25)
        logger.info("[R2] 冷启动拦截 matched=%s score=%.3f conf=%.3f",
                    r2.matched, r2.similarity, wf.confidence)
        if not r2.matched or not r2.success:
            failures.append("R2 冷启动应命中并成功执行（conf 初值 ≥ 门槛）")
        if not r2.skipped_llm:
            failures.append("R2 命中后应跳过 LLM（skipped_llm=True）")

        # ── R3-R5: 使用工作流 → 置信度单调上升 ──
        confs = []
        for label in ["R3", "R4", "R5"]:
            res = svc.execute_by_id(wf.id, TASK)
            wf_now = svc.get(wf.id)
            confs.append(round(wf_now.confidence, 2))
            logger.info("[%s] execute_by_id 成功 steps=%d conf=%.2f",
                        label, res.steps_executed, wf_now.confidence)
        if not res.success:
            failures.append(f"R3-R5 execute_by_id 应全部成功, 最后一个 success={res.success}")
        if confs != sorted(confs) or len(set(confs)) != len(confs):
            failures.append(f"R3-R5 置信度应严格单调上升, 实际 {confs}")
        logger.info("[R3-R5] 置信度轨迹: %s", confs)

        # ── R6: 拦截命中 → 短路跳过 LLM ──
        r6 = svc.try_execute(TASK, min_score=0.25)
        logger.info("[R6] 拦截命中 matched=%s score=%.3f skipped_llm=%s",
                    r6.matched, getattr(r6, "similarity", 0.0), getattr(r6, "skipped_llm", None))
        if not r6.matched or not r6.success:
            failures.append("R6 应命中并成功执行")
        if not r6.skipped_llm:
            failures.append("R6 命中后应 skipped_llm=True")

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

        # ── R9: 准入门槛（TASK-S10-01）──
        dirty = svc.learn_from_interaction(_make_dirty_record("sess-s10-01"))
        logger.info("[R9] 单轮单工具调用落库 wf=%s status=%s triggers=%s",
                    dirty.id, dirty.status, dirty.trigger_patterns)
        if dirty.status != WorkflowStatus.DRAFT.value:
            failures.append(f"R9 单步工作流应为草稿态, 实际 {dirty.status}")
        if CODE_STEPS_TOO_FEW not in dirty.description:
            failures.append("R9 草稿原因应记录步骤数下限拒绝码")
        if CODE_NO_DISCRIMINATIVE_TRIGGER not in dirty.description:
            failures.append("R9 草稿原因应记录触发词区分度拒绝码")
        r9 = svc.try_execute(DIRTY_TASK, min_score=0.25)
        logger.info("[R9] 脏条目是否被拦截 matched=%s（应为 False）", r9.matched)
        if r9.matched:
            failures.append("R9 草稿态脏条目不得进入匹配候选（不得被拦截执行）")
        if any(c["workflow_id"] == dirty.id
               for c in svc.list_convertible_workflows()):
            failures.append("R9 脏条目不得进入自动升格候选")
        try:
            # 结构性门槛在 skills_service 之前触发，故可传 None
            WorkflowToSkillConverter(None, svc.repo).convert_workflow_to_skill(
                dirty.id, force=True)
            failures.append("R9 force=True 也不得把脏条目转成 Skill")
        except WorkflowConvertError as e:
            if e.code != "QUALITY_GATE_FAILED" or CODE_STEPS_TOO_FEW not in e.codes:
                failures.append(f"R9 转技能应被结构性门槛拒绝, 实际 code={e.code} "
                                f"codes={e.codes}")
            logger.info("[R9] force 转技能已被拒: %s", str(e)[:120])
        # 草稿态仍可按 ID 人工执行（退役/隔离不删能力）
        r9b = svc.execute_by_id(dirty.id, DIRTY_TASK)
        if not (r9b.matched and r9b.success):
            failures.append("R9 草稿态条目应仍可按 ID 人工执行")

    if failures:
        logger.error("❌ 闭环模拟失败 %d 项:\n  - %s", len(failures), "\n  - ".join(failures))
        return 1
    logger.info("✅ 工作流自动闭环全部符合预期"
                "（学习→门控→收敛→拦截→降级→准入隔离）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
