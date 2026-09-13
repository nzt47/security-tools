# -*- coding: utf-8 -*-
"""工作流准入门槛 — 单字触发词 / 步骤数下限 / 跨会话样本数

背景（TASK-S10-01）
-------------------
真用期开始后，"工作流学习层"会**持续**把单轮交互学成工作流。存量里已经出现
由单轮输入自动学成的条目（`data/learned_workflows.json` 的 `wf-f19dc52c`）：

    trigger_patterns = ["列", "出", "当", "前", "工"]   # 单字
    steps            = [list_directory]                 # 仅 1 步
    source_session_id= sess_20260907_220445_39c3ebf7    # 单会话/单轮

它随后被 `convert_to_skill` 成 `wf-f19dc52c-skill`（技能产物 `来源` 块记录
`success_count: 5 / confidence: 0.75`，即**靠重复执行把统计门控刷过去**）。

本模块把这些"可判定条件"集中成**唯一判定源**，供三处复用（避免三套阈值漂移）：
    - `matcher.WorkflowMatcher`  —— 匹配候选准入（不得进入候选）
    - `learner.WorkflowLearner`  —— 学习落库时定档（不达标 → 草稿态）
    - `skill_converter` / `service.list_convertible_workflows` —— 转技能准入

阈值依据（为什么是这些值）
-------------------------
1) `MIN_STEPS = 2`（步骤数下限）
   1 步工作流 == 一次工具调用，本身没有编排价值；而它带来的成本是实打实的：
   `generator._compute_priority` 对"步骤数 ≤ 3"给 **+20 加成**，于是 `wf-f19dc52c`
   拿到 **priority=80（最高档）**——信息量最小的条目拿到最强的匹配权重（优先级
   倒挂）。同时 5 次执行一个 `list_directory` 就能把 confidence 推到 0.749
   （`record_execution` 步长公式：0.4→0.5→0.582→0.649→0.704→0.749），
   统计门控（success_count ≥ 5 / confidence ≥ 0.7）**可被重复执行刷过**，
   而"结构"刷不过去。故取更严的下限 2。

2) 触发词"长度与区分度"：`MIN_TRIGGER_CHARS = 2`、`effective_trigger_patterns`
   `learner._WORD_RE = [a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]` 对中文是**按字**切分，
   于是中文输入的 `trigger_patterns` 实际等于"用户那句话的前 5 个字"
   （词频全为 1 时按插入序取前 5），**不携带任何区分信息**：
   `wf-f19dc52c` 的 5 个触发词就是"帮我列出当前工作目录下的文件"的第 2~6 个字。
   汉字单字在任意中文任务里都高频出现，作为匹配特征等价于"任意输入都可能命中"。
   阈值取 2 的依据：中文里 2 字是最小成词单位（单字歧义率最高），
   拿不准取更严；确切的"误匹配率"实测见报告（本仓库当前索引构造下
   未复现大面积误匹配，见 §实测，故此条定位为**结构性前置条件**而非实测热区）。

3) `MIN_CROSS_SESSION_SUPPORT = 2`（最少样本数）
   "单轮来源"的机器可判定形式：同一 `task_signature` 只在**一个会话**里出现过
   ⇒ 只有 1 个样本，无从判断它是否会复现。自动升格为 Skill 属**资产沉淀**
   （会长期占用检索/注入面），故额外要求 ≥ 2 个不同会话的样本；
   人工 `force` 转换不受此条约束（人已判断）。

用法
----
    >>> from agent.workflow_learning import admission
    >>> admission.check_structure(steps=wf.steps,
    ...                           trigger_patterns=wf.trigger_patterns).admitted
    False

`force` 语义（重要）
--------------------
`force` 只允许人工越过**统计性**门控（success_count / confidence / priority /
跨会话样本数），**不得**越过**结构性**准入（步骤数下限、触发词区分度、状态）。
结构性不达标的条目不是"还没养熟的资产"，而是"根本不是资产"：
没有任何人工判断能让一次 `list_directory` 变成可复用工作流。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

__all__ = [
    "MIN_STEPS",
    "MIN_TRIGGER_CHARS",
    "MIN_CROSS_SESSION_SUPPORT",
    "CODE_STEPS_TOO_FEW",
    "CODE_NO_DISCRIMINATIVE_TRIGGER",
    "CODE_NOT_ACTIVE",
    "CODE_DISABLED",
    "CODE_SINGLE_SESSION_SUPPORT",
    "AdmissionDecision",
    "is_discriminative_trigger",
    "single_char_triggers",
    "effective_trigger_patterns",
    "check_structure",
    "check_match_eligibility",
    "check_cross_session_support",
]

# ── 阈值（策略，改动需同步 tests/unit/test_workflow_learning_admission.py）──

#: 步骤数下限：1 步 == 单次工具调用，无编排价值（且优先级倒挂给最高档）
MIN_STEPS = 2

#: 触发词"有效字符"下限：中文单字无区分度（见模块 docstring §2）
MIN_TRIGGER_CHARS = 2

#: 自动升格为 Skill 所需的最少跨会话样本数（见模块 docstring §3）
MIN_CROSS_SESSION_SUPPORT = 2

# ── 机器可读拒绝码 ──

CODE_STEPS_TOO_FEW = "STEPS_TOO_FEW"
CODE_NO_DISCRIMINATIVE_TRIGGER = "NO_DISCRIMINATIVE_TRIGGER"
CODE_NOT_ACTIVE = "NOT_ACTIVE"
CODE_DISABLED = "DISABLED"
CODE_SINGLE_SESSION_SUPPORT = "SINGLE_SESSION_SUPPORT"

#: 正则元字符不计入"有效字符"——`搜索*` 的有效长度是 2 而不是 3，
#: 而 `*`（长度 0）不应被当成有区分度的触发词（取更严）
_METACHARS = set("*?+[](){}^$|\\./")
_ACTIVE_VALUES = ("active", "WorkflowStatus.ACTIVE")


@dataclass(frozen=True)
class AdmissionDecision:
    """准入判定结果（机器可读 codes + 人类可读 reasons）"""

    codes: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return not self.codes

    @property
    def reason_text(self) -> str:
        return "；".join(self.reasons)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "admitted": self.admitted,
            "codes": list(self.codes),
            "reasons": list(self.reasons),
        }


def _informative_len(pattern: str) -> int:
    """触发词的"有效字符数"：去掉空白与正则元字符后计数"""
    return sum(1 for ch in (pattern or "")
               if not ch.isspace() and ch not in _METACHARS)


def is_discriminative_trigger(pattern: str) -> bool:
    """单个触发词是否有区分度（≥ `MIN_TRIGGER_CHARS` 个有效字符）

    `"列"` → False（单字，等价于"任意中文输入"）
    `"json"` / `"列出"` / `"搜索*"` → True
    `"*"` / `"a*"` → False（有效字符 < 2，取更严）
    """
    return _informative_len(pattern) >= MIN_TRIGGER_CHARS


def single_char_triggers(patterns: Iterable[str]) -> List[str]:
    """返回其中"单字/无有效字符"的触发词（用于证据展示与统计）"""
    return [p for p in (patterns or []) if not is_discriminative_trigger(p)]


def effective_trigger_patterns(patterns: Sequence[str]) -> List[str]:
    """只保留有区分度的触发词（单字触发词**不得**进入匹配特征/技能触发条件）"""
    return [p for p in (patterns or []) if is_discriminative_trigger(p)]


def check_structure(*, steps: Any,
                    trigger_patterns: Sequence[str]) -> AdmissionDecision:
    """结构性准入 —— 步骤数下限 + 触发词区分度（`force` 也不可越过）

    Args:
        steps: 步骤序列（或任意 len() 可用的对象）
        trigger_patterns: 工作流触发词
    """
    codes: List[str] = []
    reasons: List[str] = []

    n_steps = len(steps or [])
    if n_steps < MIN_STEPS:
        codes.append(CODE_STEPS_TOO_FEW)
        reasons.append(
            f"步骤数 {n_steps} < {MIN_STEPS}"
            f"（单步=单次工具调用，不是可复用工作流）")

    good = effective_trigger_patterns(trigger_patterns)
    if not good:
        codes.append(CODE_NO_DISCRIMINATIVE_TRIGGER)
        bad = single_char_triggers(trigger_patterns)
        if bad:
            detail = (f"有效触发词数 0 < 1；单字/空触发词 {len(bad)} 个: "
                      f"{bad[:5]}")
        else:
            # 触发词列表为空：中文按字切分得到的单字触发词已在入库前丢弃
            # （learner.effective_trigger_patterns），故这里再取严判为不达标
            detail = ("触发词列表为空（中文按字切分产生的单字触发词已在入库前"
                      "丢弃；无任何有区分度的触发词）")
        reasons.append(f"无有区分度触发词（{detail}）")

    return AdmissionDecision(codes=tuple(codes), reasons=tuple(reasons))


def check_match_eligibility(wf: Any) -> AdmissionDecision:
    """匹配候选准入 —— 结构性准入 + 启用 + ACTIVE 状态

    任一不满足即**不得进入匹配候选**（draft/archived 条目仍可按 ID 人工执行）。
    """
    codes: List[str] = []
    reasons: List[str] = []

    patterns: Sequence[str] = getattr(wf, "trigger_patterns", None) or []
    struct = check_structure(steps=getattr(wf, "steps", None),
                             trigger_patterns=patterns)
    codes.extend(struct.codes)
    reasons.extend(struct.reasons)

    if not getattr(wf, "enabled", True):
        codes.append(CODE_DISABLED)
        reasons.append("工作流未启用")

    status = getattr(wf, "status", None)
    status_val = getattr(status, "value", status)
    if str(status_val) not in _ACTIVE_VALUES:
        codes.append(CODE_NOT_ACTIVE)
        reasons.append(f"状态为 {status_val}（需 active）")

    return AdmissionDecision(codes=tuple(codes), reasons=tuple(reasons))


def check_cross_session_support(*, support_sessions: int) -> AdmissionDecision:
    """自动升格为 Skill 的样本数门槛（同一 `task_signature` 的跨会话样本数）

    仅用于"自动转技能"路径；人工 force 不适用（见模块 docstring）。
    """
    n = int(support_sessions or 0)
    if n < MIN_CROSS_SESSION_SUPPORT:
        return AdmissionDecision(
            codes=(CODE_SINGLE_SESSION_SUPPORT,),
            reasons=(f"跨会话样本数 {n} < {MIN_CROSS_SESSION_SUPPORT}"
                     f"（单会话/单轮来源，无法判断是否会复现）",),
        )
    return AdmissionDecision()
