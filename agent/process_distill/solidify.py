"""固化 — 把合并后的 DistilledProcess 固化为云枢可复用资产。

两条产物线（可分别/同时产出）：
    1. workflow：构造 LearnedWorkflow（仅保留能映射到已注册工具的步骤，
       纯指令步骤进不了执行轨）→ WorkflowGenerator.generate_and_store
       落 data/learned_workflows.json → 主循环工作流学习层 0-Token 命中；
    2. skill：编译为 skill.md（含 front matter + 步骤正文）→ 双写：
       SkillsMgmtService.create_manual（JSON 轨，管理权威/UI）
       + file_store.create（文件轨 skills_repo/<id>/skill.md，
         语义层 SkillLoader 可召回——调研确认只写 JSON 轨不会被检索）。

幂等：id 由 proc.sources 内容哈希派生，同一素材组合重复蒸馏不重复创建
      （已存在则返回已有 id，action=exists）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from agent.process_distill.models import DistilledProcess, slugify

logger = logging.getLogger(__name__)

_ID_PREFIX = "pd"  # process-distill 产物前缀


# ═══════════════════════════════════════════════════════════════
#  id 派生
# ═══════════════════════════════════════════════════════════════

def _content_hash(proc: DistilledProcess) -> str:
    h = hashlib.sha1()
    for s in proc.steps:
        h.update(f"{s.seq}:{s.action}:{s.tool}".encode("utf-8"))
    return h.hexdigest()[:8]


def _derive_id(proc: DistilledProcess, kind: str) -> str:
    """派生 workflow_id / skill_id：语义名 + 内容哈希，保证稳定与幂等。"""
    base = slugify(proc.name, max_len=48)
    h = _content_hash(proc)
    return f"{_ID_PREFIX}-{base}-{h}-{kind}"


# ═══════════════════════════════════════════════════════════════
#  工具白名单过滤
# ═══════════════════════════════════════════════════════════════

def _registered_tools() -> List[str]:
    """云枢当前已注册工具名（进程内 agent.tools 注册表）。"""
    try:
        from agent import tools as _tools
        return sorted({str(t.get("name", "")) for t in _tools.list_tools()})
    except Exception:  # noqa: BLE001  注册表不可用 → 空
        return []


# ═══════════════════════════════════════════════════════════════
#  固化为 workflow
# ═══════════════════════════════════════════════════════════════

def solidify_to_workflow(proc: DistilledProcess, *,
                         wf_svc=None, available_tools: Optional[List[str]] = None,
                         session_id: str = "process-distill") -> Dict[str, Any]:
    """固化为 LearnedWorkflow（仅含工具步骤）。

    Returns: {action: created|exists|skipped, workflow_id?, reason?}
    """
    tools = available_tools if available_tools is not None else _registered_tools()
    tool_set = set(tools or [])

    # 过滤：仅保留映射到已注册工具的步骤
    mapped = [s for s in proc.steps if s.tool and s.tool in tool_set]
    unmapped = [s for s in proc.steps if s.tool and s.tool not in tool_set]

    if not mapped:
        return {
            "action": "skipped",
            "reason": "无映射到已注册工具的步骤（纯指令流程建议固化为 skill）",
            "tool_steps": len([s for s in proc.steps if s.tool]),
            "unmapped_tools": sorted({s.tool for s in unmapped})[:10],
        }

    from agent.workflow_learning.models import LearnedWorkflow, WorkflowStep

    wf_id = _derive_id(proc, "wf")

    # 幂等：已存在直接返回
    try:
        if wf_svc is not None and wf_svc.get(wf_id):
            return {"action": "exists", "workflow_id": wf_id}
    except Exception:  # noqa: BLE001  get 可能抛（不存在/仓库异常）
        pass

    wf = LearnedWorkflow(
        id=wf_id,
        name=proc.name[:200],
        description=proc.description[:500],
        task_signature=proc.task_signature or "general",
        trigger_patterns=proc.trigger_patterns[:5],
        steps=[
            WorkflowStep(
                step_id=f"step_{i}",
                tool_name=s.tool,
                params_template=dict(s.params or {}),
                output_key=f"step_{i}_output",
                condition=s.condition or None,
                description=f"{s.action}（来源: {s.source}）"[:300],
            )
            for i, s in enumerate(mapped, start=1)
        ],
        expected_output_pattern=proc.expected_output or "",
        source_session_id=session_id,
        source_user_input=f"[知识蒸馏] {proc.name}"[:200],
        confidence=0.5,
        tags=list({*proc.tags, "distilled", "from_knowledge", "pd"})[:8],
    )

    try:
        if wf_svc is not None:
            wf_svc.generator.generate_and_store(wf)
        else:
            from agent.workflow_learning.generator import WorkflowGenerator
            from agent.workflow_learning.matcher import WorkflowMatcher
            from agent.workflow_learning.repository import WorkflowRepository
            gen = WorkflowGenerator(WorkflowRepository(), WorkflowMatcher())
            gen.generate_and_store(wf)
    except Exception as e:  # noqa: BLE001
        logger.warning("[PD] workflow 固化失败: %s", e)
        return {"action": "error", "error": str(e), "workflow_id": wf_id}

    result: Dict[str, Any] = {"action": "created", "workflow_id": wf_id,
                              "steps": len(mapped)}
    if unmapped:
        result["unmapped_steps"] = len(unmapped)
        result["unmapped_tools"] = sorted({s.tool for s in unmapped})[:10]
    return result


# ═══════════════════════════════════════════════════════════════
#  固化为 skill
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  质量门槛（固化前）
# ═══════════════════════════════════════════════════════════════

# 规则降级(无 LLM)产物至少需要的实质步骤数；低于此视为"没内容"拒识
_MIN_RULE_STEPS = 3
# LLM 产物至少需要的步骤数（LLM 高质量，门槛更低）
_MIN_LLM_STEPS = 1


def _quality_check(proc: DistilledProcess) -> Optional[str]:
    """固化前质量自检。通过返回 None；不通过返回拒识原因。"""
    if not proc.name or not proc.name.strip():
        return "产物名称为空，拒识"
    n = len(proc.steps)
    if proc.method == "rule":
        if n < _MIN_RULE_STEPS:
            return (f"规则降级产物步骤过少({n} < {_MIN_RULE_STEPS})，"
                    "疑似无实质内容，拒识——建议改用 LLM 蒸馏或检查素材")
    else:
        if n < _MIN_LLM_STEPS:
            return f"蒸馏产物无步骤({n})，拒识"
    # 无工具步骤时全部步骤都含明显噪声标记 → 拒识（动作文本已由
    # prompts._clean_action 清洗，这里只兜底明显空壳）
    if proc.steps and all(
            not (s.action or "").strip() for s in proc.steps):
        return "所有步骤均为空文本，拒识"
    return None


def _compile_skill_content(proc: DistilledProcess) -> str:
    """编译 skill.md 正文（markdown 步骤清单，含来源与边界）。"""
    lines: List[str] = []
    lines.append(f"# {proc.name}")
    lines.append("")
    if proc.description:
        lines.append(proc.description)
        lines.append("")
    lines.append("## 触发条件")
    for t in proc.trigger_patterns or []:
        lines.append(f"- `{t}`")
    if not proc.trigger_patterns:
        lines.append(f"- 任务签名: `{proc.task_signature}`")
    lines.append("")
    lines.append("## 步骤清单")
    if not proc.steps:
        lines.append("（无步骤）")
    else:
        for s in proc.steps:
            tool_tag = f"`{s.tool}`" if s.tool else "（纯指令）"
            lines.append(f"### 步骤 {s.seq}: {tool_tag}")
            lines.append(f"- {s.action}")
            if s.params:
                import json
                lines.append("  **参数:**")
                lines.append("  ```json")
                lines.append("  " + json.dumps(s.params, ensure_ascii=False))
                lines.append("  ```")
            if s.condition:
                lines.append(f"- 条件: `{s.condition}`")
            if s.note:
                lines.append(f"- 边界: {s.note}")
            lines.append("")
    lines.append("## 来源")
    for src in proc.sources:
        lines.append(f"- {src}")
    return "\n".join(lines)


def solidify_to_skill(proc: DistilledProcess, *, skills_svc=None,
                      run_review: bool = True,
                      ) -> Dict[str, Any]:
    """固化为 skill（JSON 轨 + 文件轨双写，可选正式评审）。

    流程：
        1. 质量门槛自检（_quality_check）——不通过返回 action=skipped；
        2. create_manual 落 JSON 轨（status=draft，自动触发咨询性 digest）；
        3. file_store.create 落文件轨（meta 补 status/enabled，双轨一致）；
        4. run_review=True 时调 review() 走正式三审——PASSED→APPROVED、
           WARN→PENDING_REVIEW、FAILED→REJECTED（仍不自动 publish，
           发布留人工；符合云枢"AI 只产草稿、审核放行"原则）。

    Returns: {action: created|exists|skipped|error, skill_id?, ...}
    """
    # C. 质量门槛（固化前拒识）
    quality_reason = _quality_check(proc)
    if quality_reason:
        return {
            "action": "skipped",
            "reason": quality_reason,
            "skill_id": "",
            "method": proc.method,
            "steps": len(proc.steps),
        }

    skill_id = _derive_id(proc, "skill")

    # 已存在（任一轨）→ exists
    try:
        if skills_svc is not None and skills_svc.get(skill_id):
            return {"action": "exists", "skill_id": skill_id}
    except Exception:  # noqa: BLE001
        pass

    content = _compile_skill_content(proc)
    meta = {
        "id": skill_id,
        "name": proc.name[:200],
        "description": proc.description[:300] or f"由知识蒸馏生成: {proc.name}",
        "content_type": "markdown",
        "category": "custom",
        "tags": list({*proc.tags, "distilled", "from_knowledge", "external"})[:8],
        "author": "process_distill",
        "source": "knowledge_distill",
        # A. 双轨一致：文件轨显式声明状态与启停（与 JSON 轨 draft/enabled 对齐），
        #    避免文件轨技能状态悬空（此前无 status/enabled → 面板显示"仅元数据"）
        "status": "draft",
        "enabled": True,
        "version": "0.1.0",
    }
    data = dict(meta)
    data["content"] = content

    try:
        if skills_svc is None:
            from agent.skills_mgmt import SkillsMgmtService
            skills_svc = SkillsMgmtService()

        # JSON 轨（管理权威 + UI + 审核链路）
        created = skills_svc.create_manual(data)
        # 文件轨（语义层检索）：repo 路径与 JSON 轨同目录 skills_repo
        try:
            skills_svc.file_store.create(
                skill_id,
                meta=dict(meta),
                instruction=content,
            )
        except Exception as fe:  # noqa: BLE001  文件轨失败不阻断（JSON 轨已成功）
            logger.warning("[PD] skill 文件轨写入失败（JSON 轨已成功）: %s", fe)
            return {"action": "created", "skill_id": skill_id,
                    "json_track": True, "file_track": False,
                    "file_error": str(fe)[:200]}

        result: Dict[str, Any] = {
            "action": "created", "skill_id": skill_id,
            "json_track": True, "file_track": True,
            "status": "draft",
        }

        # B. 正式评审-消化（权威三审；通过→APPROVED，仍不自动 publish）
        if run_review:
            try:
                r = skills_svc.review(skill_id)
                rev_status = getattr(r.status, "value", r.status)
                result["review"] = {
                    "status": rev_status,
                    "verdict": getattr(r.digest_verdict, "value",
                                       r.digest_verdict)
                    or rev_status,
                    "score": getattr(r, "score", None),
                    "summary": getattr(r, "summary", "")[:200],
                }
                # review 可能已把技能状态推进（PASSED→approved 等），回读
                try:
                    cur = skills_svc.get(skill_id)
                    final_status = getattr(cur, "status", "draft")
                    result["status"] = final_status
                    # A. 双轨最终一致：把权威状态同步到文件轨 front matter
                    if final_status != "draft":
                        try:
                            skills_svc.file_store.update_meta(
                                skill_id, {"status": final_status})
                        except Exception as ue:  # noqa: BLE001
                            logger.warning(
                                "[PD] skill 文件轨状态同步失败（JSON 轨已权威）: %s",
                                ue)
                except Exception:  # noqa: BLE001
                    pass
            except Exception as ree:  # noqa: BLE001  评审失败不阻断固化
                logger.warning("[PD] skill 正式评审失败（已固化，状态 draft）: %s",
                               ree)
                result["review"] = {"error": str(ree)[:200]}

        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("[PD] skill 固化失败: %s", e)
        return {"action": "error", "error": str(e), "skill_id": skill_id}
