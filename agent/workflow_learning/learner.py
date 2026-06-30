"""学习者 — 从 LLM 交互中提取可复用方法

输入: LearningRecord (一次成功的 LLM 交互记录)
输出: LearnedWorkflow 骨架 (尚未保存到仓库)

学习方法:
    1. 抽取工具调用序列: tool_calls 中按时间顺序提取 tool_name → WorkflowStep
    2. 参数模板化: 把具体参数值替换为 $input / $prev_output / 字面量占位
    3. 任务签名: 提取用户输入的关键词组合 (去停用词)
    4. 触发模式: 从用户输入提取 3-5 个关键词
    5. 置信度初值: 0.3 (待后续执行累积)
"""

from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import LearnedWorkflow, WorkflowStep, LearningRecord
from .exceptions import WorkflowLearningError, ErrorCode
from .observability import logger, emit_metric, track_event, traced_action

# 简易停用词表 (中英文混合)
_STOP_WORDS = {
    # 中文
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "们",
    "和", "与", "或", "及", "但", "而", "请", "帮", "给", "把",
    "这", "那", "一", "二", "三", "个", "中", "上", "下",
    # 英文
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "by",
    "and", "or", "but", "if", "then", "so", "do", "does", "did",
    "i", "you", "he", "she", "it", "we", "they",
    "please", "help", "me", "my", "your",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _extract_keywords(text: str, top_k: int = 5) -> List[str]:
    """提取关键词 (去停用词，按频率取 top_k)"""
    tokens = _WORD_RE.findall((text or "").lower())
    freq: Dict[str, int] = {}
    for t in tokens:
        if t in _STOP_WORDS or len(t) < 1:
            continue
        freq[t] = freq.get(t, 0) + 1
    return [t for t, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_k]]


def _make_task_signature(user_input: str) -> str:
    """生成任务签名 (关键词按字典序拼接，便于去重)"""
    kws = sorted(set(_extract_keywords(user_input, top_k=10)))
    return "|".join(kws) or "general"


def _templatize_params(params: Dict[str, Any], *, user_input: str) -> Dict[str, Any]:
    """把参数值模板化

    简化策略:
        - 字符串值若包含 user_input 关键词，替换为 $input
        - 字符串值若像 URL/路径，保持原样
        - 其他保持字面量
    """
    if not params:
        return {}
    template: Dict[str, Any] = {}
    keywords = _extract_keywords(user_input, top_k=3)
    for k, v in params.items():
        if isinstance(v, str):
            # 如果值里包含用户输入的关键词，模板化
            for kw in keywords:
                if kw and kw in v.lower():
                    v = v.lower().replace(kw, "${input}")
                    break
            template[k] = v
        else:
            template[k] = v
    return template


class WorkflowLearner:
    """工作流学习者"""

    def learn(self, record: LearningRecord) -> LearnedWorkflow:
        """从一次成功的 LLM 交互中学习方法"""
        with traced_action("wf_learn", session_id=record.session_id,
                           user_input=record.user_input[:80]) as ctx:
            if not record.success:
                raise WorkflowLearningError(
                    "仅能从成功的交互中学习",
                    code=ErrorCode.LEARN_FAILED,
                    details={"success": record.success},
                )
            if not record.tool_calls:
                raise WorkflowLearningError(
                    "工具调用序列为空，无可学习方法",
                    code=ErrorCode.LEARN_FAILED,
                )

            # 1) 提取步骤
            steps = self._extract_steps(record)
            if not steps:
                raise WorkflowLearningError(
                    "无法从 tool_calls 中提取有效步骤",
                    code=ErrorCode.LEARN_FAILED,
                )

            # 2) 任务签名 & 触发模式
            signature = _make_task_signature(record.user_input)
            triggers = _extract_keywords(record.user_input, top_k=5)

            # 3) 生成工作流
            wf_id = self._derive_id(record, signature)
            wf = LearnedWorkflow(
                id=wf_id,
                name=self._derive_name(record, triggers),
                description=f"从会话 {record.session_id} 中学习得到。"
                            f"原始任务: {record.user_input[:100]}",
                task_signature=signature,
                trigger_patterns=triggers,
                steps=steps,
                source_session_id=record.session_id,
                source_user_input=record.user_input[:500],
                confidence=0.3,  # 初始低置信度，待执行验证
                priority=50,
                tags=["learned"] + triggers[:3],
            )
            ctx["workflow_id"] = wf.id
            ctx["steps"] = len(steps)
            track_event("wf_learned", {
                "workflow_id": wf.id, "session_id": record.session_id,
                "steps": len(steps),
            })
            emit_metric("yunshu_wf_learned_total",
                        labels={"success": "true"}, kind="counter")
            logger.info("[Learner] 学习到工作流 %s (%d 步) 来自 session %s",
                        wf.id, len(steps), record.session_id)
            return wf

    # ─── 步骤提取 ───

    def _extract_steps(self, record: LearningRecord) -> List[WorkflowStep]:
        steps: List[WorkflowStep] = []
        for i, call in enumerate(record.tool_calls):
            name = call.get("name") or call.get("tool") or ""
            if not name:
                continue
            params = call.get("params") or call.get("arguments") or {}
            templated = _templatize_params(params, user_input=record.user_input)
            steps.append(WorkflowStep(
                step_id=f"step_{i+1}",
                tool_name=name,
                params_template=templated,
                output_key=f"step_{i+1}_output",
                description=call.get("description", ""),
            ))
        return steps

    # ─── ID 与名称生成 ───

    @staticmethod
    def _derive_id(record: LearningRecord, signature: str) -> str:
        """从签名 + 时间戳生成 ID"""
        import hashlib
        h = hashlib.sha256(
            f"{signature}:{record.session_id}:{record.user_input}".encode("utf-8")
        ).hexdigest()[:8]
        # 取签名第一个关键词作前缀
        prefix = signature.split("|", 1)[0][:16] or "wf"
        prefix = re.sub(r"[^a-z0-9\-]", "", prefix.lower()) or "wf"
        return f"{prefix}-{h}"

    @staticmethod
    def _derive_name(record: LearningRecord, triggers: List[str]) -> str:
        if triggers:
            return f"自动学习: {'-'.join(triggers[:3])}"
        return f"自动学习工作流 ({record.session_id[:8]})"
