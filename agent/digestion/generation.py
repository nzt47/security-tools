"""候选模式 → SKILL.md 草稿（TASK-S3-01 步骤 3 的"生成"半段）

**不新建第三套固化逻辑**（任务书硬要求）——本模块只做桥接与编排：

| 环节 | 复用 | 说明 |
|---|---|---|
| 正文编译（front matter + 步骤正文） | `agent/process_distill/solidify._compile_skill_content` | 既有 markdown 编译器，逐字复用 |
| front matter 序列化 | `agent/skills_mgmt/file_store.SkillMDParser.serialize` | 既有白名单序列化器（`_META_FIELDS`） |
| 质量门控升格 | `agent/workflow_learning/skill_converter` 的阈值词汇 | 阈值与本模块常量对齐（用例断言一致，防漂移） |
| 真正落技能主轨 | `solidify.solidify_to_skill` / `WorkflowToSkillConverter` | **本任务默认不调用**；只提供 opt-in 桥接 |

**draft 纪律（验收硬项）**：

- 产物 `status="draft"`、`enabled=False`，落盘只到草稿暂存区
  （``data/digestion/drafts/<skill_id>/SKILL.md``），**不进** `skills_repo/`；
- 正文首部插入"自动挖掘草稿"横幅，人工可见；
- **绝不**调用 `publish` / `optimize_with_feedback`（后者会把 approved 静默升为
  published）；若走 `solidify_to_skill` 桥接，强制 `run_review=False`
  —— 因为 `run_review=True` 是默认值且会经 `SkillReviewer` 把技能改成
  `approved/pending_review/rejected`，属"越过审批"。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .generalize import is_placeholder
from .models import CandidatePattern, SkillDraft

logger = logging.getLogger("agent.digestion.generation")

#: 产物前缀（与 `process_distill` 的 `pd-`、`workflow_learning` 的 `-skill` 区分）
DRAFT_ID_PREFIX = "dig"
#: 草稿暂存目录（**非** skills_repo；运行时不会加载此目录）
DEFAULT_DRAFT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", "drafts")

#: 质量门控阈值 —— 与 `agent.workflow_learning.skill_converter` 的
#: `MIN_SUCCESS_COUNT` / `MIN_CONFIDENCE` / `MIN_PRIORITY` **同词汇**。
#: `test_digestion_generation.py::test_gate_constants_align_with_skill_converter`
#: 断言二者相等，防止两套阈值随版本漂移。
MIN_SUCCESS_COUNT = 5          # = skill_converter.MIN_SUCCESS_COUNT
MIN_CONFIDENCE = 0.7           # = skill_converter.MIN_CONFIDENCE
MIN_PRIORITY = 50              # = skill_converter.MIN_PRIORITY
#: 消化侧附加门：骨架覆盖率下限（低于此值说明"骨架"不足以代表该类任务）
MIN_PATTERN_COVERAGE = 0.6

#: **升格前门**的步骤数下限 —— 必须 ≥ 既有 `solidify._quality_check` 对
#: `method="rule"` 的门槛（`_MIN_RULE_STEPS = 3`）。理由：本模块的门是"升格前总门"，
#: 若比下一跳更松，"已过门"的产物会在 `solidify_to_skill` 处被**静默拒绝**
#: （返回 `{"action":"skipped","reason":"规则降级产物步骤过少…"}`）——
#: 那是实现期实测到的真实接缝，故把两处拉平为同一口径。
#: 由 `test_digestion_generation.py::test_ascension_steps_align_with_solidify`
#: 与 `solidify._MIN_RULE_STEPS` 逐值对账，防漂移。
#:
#: 注意与 `models.MIN_PATTERN_STEPS`（=2）语义不同：后者回答"骨架是否成形"
#: （用于 S3-02 判定集），本常量回答"产物能否经既有门控升格"。两问不同，故两值不同。
MIN_ASCENSION_STEPS = 3

#: 草稿横幅（人工可见的"未审核"提示）
DRAFT_BANNER = (
    "> ⚠️ **自动挖掘草稿（draft）**：本文件由消化流水线（TASK-S3-01）从统一轨迹台账\n"
    "> 挖掘生成，**未经人工审核、未发布、未启用**。发布须经 S3-02 判定集 / S3-03\n"
    "> shadow 灰度与既有评审门，切勿直接投产。\n"
)

_ID_SAFE_RE = re.compile(r"[^a-z0-9\-]+")


# ════════════════════════════════════════════════════════════
#  质量门控
# ════════════════════════════════════════════════════════════


def converter_gate_constants() -> Tuple[int, float, int]:
    """读取 `skill_converter` 的真实阈值（懒加载；供一致性用例使用）"""
    from agent.workflow_learning.skill_converter import (  # noqa: WPS433
        MIN_CONFIDENCE as _mc, MIN_PRIORITY as _mp, MIN_SUCCESS_COUNT as _ms)
    return int(_ms), float(_mc), int(_mp)


def solidify_min_rule_steps() -> int:
    """读取 `solidify._quality_check` 对 `method="rule"` 的真实步骤数门槛

    懒加载（避免把 process_distill 拉进常规导入路径），供
    `test_ascension_steps_align_with_solidify` 与 `MIN_ASCENSION_STEPS` 对账。
    """
    from agent.process_distill.solidify import _MIN_RULE_STEPS
    return int(_MIN_RULE_STEPS)


def pattern_quality_gate(pattern: CandidatePattern) -> Tuple[bool, List[str]]:
    """候选模式**升格前总门**（与既有 converter / solidify 同口径）

    门控项（任一不过即标注"未达升格门槛"；**不妨碍** draft 草稿产出 —— 草稿仍然
    生成，只是如实标注，把判断留给人工与 S3-02/S3-03）：

    1. 支撑轨迹数 ≥ ``MIN_SUCCESS_COUNT``（= `skill_converter.MIN_SUCCESS_COUNT`）
    2. 置信度 ≥ ``MIN_CONFIDENCE``（同上）
    3. 骨架覆盖率 ≥ ``MIN_PATTERN_COVERAGE``
    4. 骨架步骤数 ≥ ``MIN_ASCENSION_STEPS``（= `solidify._MIN_RULE_STEPS`；
       与 mining 侧 `MIN_PATTERN_STEPS` 语义不同，见常量注释）
    """
    reasons: List[str] = []
    if pattern.support < MIN_SUCCESS_COUNT:
        reasons.append(f"支撑轨迹数 {pattern.support} < {MIN_SUCCESS_COUNT}")
    if pattern.confidence < MIN_CONFIDENCE:
        reasons.append(f"置信度 {pattern.confidence:.2f} < {MIN_CONFIDENCE}")
    if pattern.coverage < MIN_PATTERN_COVERAGE:
        reasons.append(f"骨架覆盖率 {pattern.coverage:.2f} < {MIN_PATTERN_COVERAGE}")
    if len(pattern.steps) < MIN_ASCENSION_STEPS:
        reasons.append(
            f"骨架步骤数 {len(pattern.steps)} < {MIN_ASCENSION_STEPS}"
            f"（既有 solidify 规则门；低于此值升格会被静默拒绝）")
    return (not reasons), reasons


def pattern_to_priority(pattern: CandidatePattern) -> int:
    """模式 → 优先级（对齐 converter 的 0–100 语义；由支撑率/覆盖率/置信度合成）"""
    score = 100.0 * (
        0.4 * min(1.0, pattern.support / max(1, pattern.sample_size))
        + 0.3 * pattern.coverage + 0.3 * pattern.confidence)
    return max(0, min(100, int(round(score))))


# ════════════════════════════════════════════════════════════
#  候选模式 → DistilledProcess（复用既有正文编译器）
# ════════════════════════════════════════════════════════════


def _slugify(text: str, max_len: int = 48) -> str:
    from agent.process_distill.models import slugify
    return slugify(text, max_len=max_len)


def draft_skill_id(pattern: CandidatePattern) -> str:
    """稳定草稿 skill_id（``dig-<slug>-<hash8>``；符合 skills_mgmt 的 id 正则）

    哈希取自"模式身份"（pattern_id + 同类判定键 + 骨架长度）⇒ 同一模式恒得同一
    id（幂等，可重复生成不产生新技能），不同模式必得不同 id。
    """
    import hashlib

    material = f"{pattern.pattern_id}|{pattern.key.as_str()}|{pattern.lcs_length}"
    short = hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]
    slug = _ID_SAFE_RE.sub("-", _slugify(pattern.key.capability_id or
                                        pattern.pattern_id, max_len=40)).strip("-")
    return f"{DRAFT_ID_PREFIX}-{slug or 'pattern'}-{short}"


def pattern_to_distilled_process(pattern: CandidatePattern) -> Any:
    """候选模式 → `DistilledProcess`（复用既有模型，不另造结构）

    映射：骨架步骤 → `DistilledStep`（``tool`` = canonical capability_id，
    ``params`` = 参数槽占位符，``condition`` = 该位次的分支条件，``note`` = 支撑率），
    参数槽/branch → 说明文本写入 `description` 与 `sources` 供人工核对。
    """
    from agent.process_distill.models import DistilledProcess, DistilledStep

    branches_at: Dict[int, List[str]] = {}
    for branch in pattern.branches:
        branches_at.setdefault(branch.at_step, []).append(branch.condition)

    steps: List[DistilledStep] = []
    for step in pattern.steps:
        params: Dict[str, Any] = {}
        for key, value in (step.params or {}).items():
            params[key] = value
        for slot in pattern.slots:
            if slot.step_label and slot.step_label != step.label:
                continue
            params.setdefault(slot.name, slot.placeholder)
        conditions = branches_at.get(step.seq, [])
        note = f"支撑率 {step.support:.0%}（{step.samples} 条同类轨迹）"
        if step.optional:
            note += "；可选步骤"
        steps.append(DistilledStep(
            seq=step.seq,
            action=_step_action_text(step.label, step.capability_id),
            tool=step.capability_id or step.label,
            params=params,
            condition=_clean_condition(
                step.condition or ("；".join(conditions) if conditions else "")),
            note=note,
            source=f"digestion:{pattern.pattern_id}",
            confidence=round(min(1.0, step.support), 4),
        ))

    description = (
        f"由消化流水线从 {pattern.support}/{pattern.sample_size} 条同类轨迹挖掘的候选模式"
        f"（覆盖率 {pattern.coverage:.0%}，LCS 骨架长度 {pattern.lcs_length}，"
        f"置信度 {pattern.confidence:.2f}）。"
        f"同类判定键：{pattern.key.as_str()}。"
        + (f" 参数槽：{', '.join(s.placeholder for s in pattern.slots)}。"
           if pattern.slots else " 无跨轨迹差异参数（骨架参数为字面量）。")
        + (f" 分支条件 {len(pattern.branches)} 条。"
           if pattern.branches else "")
    )
    return DistilledProcess(
        name=f"消化草稿 {pattern.key.capability_id}",
        description=description,
        task_signature=pattern.key.as_str(),
        trigger_patterns=list(dict.fromkeys(
            [pattern.key.capability_id,
             f"intent-key={pattern.key.intent_key}"])),
        steps=steps,
        expected_output=f"完成 {pattern.key.capability_id} 类任务并产出可验证结果",
        sources=[f"trace-set:{pattern.key.as_str()}",
                 f"pattern:{pattern.pattern_id}",
                 f"method:{pattern.method}"],
        method="rule",
        tags=["from_digestion", "draft", pattern.key.capability_id],
    )


def _step_action_text(label: str, capability_id: str) -> str:
    """步骤动作描述（人可读；不引入模型调用，纯确定性）"""
    return f"调用能力 `{capability_id or label}`（归一标签：{label}）"


def _clean_condition(text: str) -> str:
    """分支条件 → 内嵌安全文本

    `solidify._compile_skill_content` 会把条件包进一层反引号，而我们的条件本身
    用反引号标注步骤名（``` `write_file` ```）—— 直接内嵌会产出嵌套反引号、
    破坏 markdown。此处剥掉内层反引号，保留可读文本。
    """
    return str(text or "").replace("`", "").strip()


# ════════════════════════════════════════════════════════════
#  编译 SKILL.md（正文复用 solidify，front matter 复用 SkillMDParser）
# ════════════════════════════════════════════════════════════


def compile_skill_body(pattern: CandidatePattern) -> str:
    """候选模式 → SKILL.md **正文**（复用 `solidify._compile_skill_content`）"""
    from agent.process_distill.solidify import _compile_skill_content

    proc = pattern_to_distilled_process(pattern)
    body = _compile_skill_content(proc)
    header = [
        DRAFT_BANNER,
        f"## 副作用画像",
        "- 写入: " + (", ".join(pattern.side_effect_profile.get(
            "files_written_shape") or []) or "（无）"),
        "- 删除: " + (", ".join(pattern.side_effect_profile.get(
            "files_deleted_shape") or []) or "（无）"),
        "- 外部调用: " + (", ".join(pattern.side_effect_profile.get(
            "external_calls_shape") or []) or "（无）"),
        f"- 回滚提示: {pattern.side_effect_profile.get('undo_hint', '')}",
        f"- 负样本: {pattern.negative_samples} 条同类失败轨迹（未参与骨架，"
        f"仅供评测与失败规避）",
        "",
    ]
    if pattern.branches:
        header.append("## 分支条件（决策树提取）")
        for branch in pattern.branches:
            where = f"第 {branch.at_step} 步" if branch.at_step > 0 else "整条轨迹"
            header.append(
                f"- [{where}] {branch.condition} → 历史倾向 **{branch.outcome}**"
                f"（{branch.support}/{branch.total}，{branch.support_ratio:.0%}）"
                + (f"｜{branch.advice}" if branch.advice else ""))
        header.append("")
    return "\n".join(header) + "\n" + body


def draft_front_matter(pattern: CandidatePattern, skill_id: str) -> Dict[str, Any]:
    """草稿 front matter（字段须落在 `file_store._META_FIELDS` 白名单内）"""
    ok, reasons = pattern_quality_gate(pattern)
    return {
        "id": skill_id,
        "name": f"[草稿] {pattern.key.capability_id} 消化模式",
        "description": (f"自动挖掘草稿：来自 {pattern.support} 条同类轨迹，"
                        f"覆盖率 {pattern.coverage:.0%}。"
                        + ("**未达升格门槛**：" + "；".join(reasons) if not ok
                           else "已达升格门槛，待人工与 S3-02/S3-03 验收")),
        "category": "custom",
        "tags": ["from_digestion", "draft", "auto_mined",
                 pattern.key.capability_id],
        "version": "0.1.0",
        # draft 纪律：不启用、不发布
        "enabled": False,
        "status": "draft",
        "author": "digestion_pipeline",
        "source": "digestion",
        "content_type": "markdown",
        "default_params": {
            "pattern_id": pattern.pattern_id,
            "capability_id": pattern.key.capability_id,
            "same_task_key": pattern.key.as_str(),
        },
        "dependencies": sorted({s.capability_id for s in pattern.steps
                                if s.capability_id}),
    }


def build_skill_draft(pattern: CandidatePattern,
                      *, skill_id: str = "") -> SkillDraft:
    """候选模式 → `SkillDraft`（draft 态；**不落盘、不发布**）"""
    sid = skill_id or draft_skill_id(pattern)
    meta = draft_front_matter(pattern, sid)
    body = compile_skill_body(pattern)
    try:
        from agent.skills_mgmt.file_store import SkillMDParser
        markdown = SkillMDParser.serialize(meta, body)
    except Exception as e:  # noqa: BLE001  序列化器不可用 → 内联等价 front matter
        logger.debug("SkillMDParser 不可用，回退内联 front matter: %s", e)
        import yaml
        markdown = ("---\n"
                    + yaml.safe_dump(meta, allow_unicode=True,
                                     default_flow_style=False,
                                     sort_keys=False).strip()
                    + "\n---\n\n" + body)
    return SkillDraft(
        skill_id=sid,
        name=meta["name"],
        description=meta["description"],
        markdown=markdown,
        pattern_id=pattern.pattern_id,
        status="draft",
        tags=list(meta["tags"]),
        front_matter=meta,
        source_track="digestion",
    )


def persist_skill_draft(draft: SkillDraft, *,
                        draft_dir: str = "") -> str:
    """草稿落盘到**暂存区**（``<draft_dir>/<skill_id>/SKILL.md``）

    **不写** `skills_repo/`、不触碰技能主轨、不触发布门 —— 草稿只是文件，
    是 S3-02/S3-03 与人工审核的输入。返回落盘路径（失败返回 ""）。
    """
    base = draft_dir or DEFAULT_DRAFT_DIR
    try:
        target_dir = os.path.join(base, draft.skill_id)
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, "SKILL.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(draft.markdown)
        draft.path = path
        return path
    except Exception as e:  # noqa: BLE001  落盘失败 advisory，不影响报告产出
        logger.warning("草稿落盘失败 %s: %s", draft.skill_id, e)
        return ""


# ════════════════════════════════════════════════════════════
#  opt-in 升格桥接（默认不被流水线调用；留 S3-02/S3-03 与人工通道）
# ════════════════════════════════════════════════════════════


def to_learned_workflow(pattern: CandidatePattern) -> Any:
    """候选模式 → `LearnedWorkflow`（供既有
    `WorkflowToSkillConverter.convert_workflow_to_skill` 做质量门控升格）

    ``workflow_type="toolchain"`` 故不允许 ``need_llm`` 步骤（模型校验强制）；
    统计量按门控语义如实填充：``success_count`` = 支撑轨迹数，
    ``confidence`` = 模式置信度，``priority`` = `pattern_to_priority()`。
    """
    from agent.workflow_learning.models import LearnedWorkflow, WorkflowStep

    ok, reasons = pattern_quality_gate(pattern)
    wf_steps: List[Any] = []
    for i, pstep in enumerate(pattern.steps):
        params: Dict[str, Any] = {}
        for slot in pattern.slots:
            if not slot.step_label or slot.step_label == pstep.label:
                params.setdefault(slot.name, slot.placeholder)
        wf_steps.append(WorkflowStep(
            step_id=f"s{i + 1}",
            tool_name=pstep.capability_id or pstep.label,
            params_template=params,
            output_key="",
            output_schema=None,
            condition=pstep.condition or None,
            description=_step_action_text(pstep.label, pstep.capability_id),
            need_llm=False,
            prompt_template="",
            timeout_ms=60000,
        ))
    wf_id = _ID_SAFE_RE.sub("-", (draft_skill_id(pattern))).strip("-") or "dig-wf"
    return LearnedWorkflow(
        id=wf_id,
        name=f"消化模式 {pattern.key.capability_id}",
        description=(f"消化流水线候选模式（{pattern.support}/{pattern.sample_size}，"
                     f"覆盖率 {pattern.coverage:.0%}）"
                     + ("" if ok else "；未达升格门槛：" + "；".join(reasons))),
        task_signature=pattern.key.as_str(),
        trigger_patterns=list(dict.fromkeys(
            [pattern.key.capability_id,
             f"intent-key={pattern.key.intent_key}"])),
        steps=wf_steps,
        expected_output_pattern="",
        source_session_id="",
        source_user_input="",
        success_count=pattern.support,
        failure_count=pattern.negative_samples,
        confidence=min(1.0, max(0.0, pattern.confidence)),
        priority=pattern_to_priority(pattern),
        tags=["from_digestion", "draft", pattern.key.capability_id],
        workflow_type="toolchain",
    )


def promote_workflow_to_skill(pattern: CandidatePattern, *, converter: Any,
                              force: bool = False) -> Dict[str, Any]:
    """**opt-in** 升格桥接：候选模式 → 既有质量门控 → 既有技能创建通道

    本函数**不**被 `DigestionService.pipeline()` 调用（流水线只产 draft）。
    它是给 S3-02/S3-03 验收通过后的人工/流程通道预留的单一入口，避免第三套
    固化逻辑。质量门控由既有 `WorkflowToSkillConverter._check_quality_gate`
    执行（阈值 `MIN_SUCCESS_COUNT`/`MIN_CONFIDENCE`/`MIN_PRIORITY`）。
    """
    wf = to_learned_workflow(pattern)
    result: Dict[str, Any] = converter.convert_workflow_to_skill(wf.id,
                                                                force=force)
    return result


def solidify_draft(pattern: CandidatePattern, *, skills_svc: Any) -> Dict[str, Any]:
    """**opt-in** 桥接既有 `solidify_to_skill`（强制 ``run_review=False``）

    `run_review=True` 是 `solidify_to_skill` 的**默认值**且会经 `SkillReviewer`
    改写技能状态（approved/pending_review/rejected），属"越过审批"——本桥接
    显式关闭它，保证产物停在 draft。同样**不**被流水线自动调用。
    """
    from agent.process_distill.solidify import solidify_to_skill

    return solidify_to_skill(pattern_to_distilled_process(pattern),
                             skills_svc=skills_svc, run_review=False)


__all__ = [
    "DRAFT_ID_PREFIX", "DEFAULT_DRAFT_DIR", "DRAFT_BANNER",
    "MIN_SUCCESS_COUNT", "MIN_CONFIDENCE", "MIN_PRIORITY",
    "MIN_PATTERN_COVERAGE", "MIN_ASCENSION_STEPS",
    "converter_gate_constants", "solidify_min_rule_steps",
    "pattern_quality_gate", "pattern_to_priority",
    "draft_skill_id", "pattern_to_distilled_process", "compile_skill_body",
    "draft_front_matter", "build_skill_draft", "persist_skill_draft",
    "to_learned_workflow", "promote_workflow_to_skill", "solidify_draft",
]
