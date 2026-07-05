"""工作流 → 技能 转换器

将"高频复用且高置信度"的 LearnedWorkflow 自动抽象为 Skill，
让通过对话学到的工作流沉淀为可复用的技能资产。

转换门控:
    - success_count >= MIN_SUCCESS_COUNT (默认 5)
    - confidence >= MIN_CONFIDENCE (默认 0.7)
    - status == ACTIVE
    - 未转换过 (converted_to_skill_id == "")

外部技能翻译:
    - convert_external_skill(external_data, llm_client) 调用 LLM
      把其他 agent 的技能描述 (JSON/YAML) 翻译为云枢 SKILL.md 格式

设计原则:
    - 边界显性化: 不满足门控抛 WorkflowConvertError
    - 后端权威: 转换完成后回写 converted_to_skill_id 到 workflow
    - 可观测: 全程结构化日志 + track_event 埋点
    - 幂等: 重复调用 convert_workflow_to_skill 返回已存在的 skill_id
"""
from __future__ import annotations

import json
import re
import time
import logging
from typing import Any, Dict, List, Optional

from .models import LearnedWorkflow, WorkflowStatus
from .observability import logger, emit_metric, track_event, traced_action


# ═══════════════════════════════════════════════════════════════
#  转换异常
# ═══════════════════════════════════════════════════════════════

class WorkflowConvertError(Exception):
    """工作流转换异常"""

    def __init__(self, message: str, *, code: str = "CONVERT_FAILED",
                 workflow_id: str = ""):
        super().__init__(message)
        self.code = code
        self.workflow_id = workflow_id


# ═══════════════════════════════════════════════════════════════
#  质量门控阈值
# ═══════════════════════════════════════════════════════════════

MIN_SUCCESS_COUNT = 5        # 至少成功 5 次才考虑转换
MIN_CONFIDENCE = 0.7         # 置信度阈值
MIN_PRIORITY = 50            # 默认优先级门控


# ═══════════════════════════════════════════════════════════════
#  转换器
# ═══════════════════════════════════════════════════════════════

class WorkflowToSkillConverter:
    """把 LearnedWorkflow 编译为 Skill 并注册到 skills_mgmt

    依赖注入:
        skills_service: SkillsMgmtService 实例（避免循环依赖，运行时注入）
        repo: WorkflowRepository 实例（用于回写 converted_to_skill_id）
    """

    def __init__(self, skills_service, repo):
        """
        Args:
            skills_service: SkillsMgmtService 实例
            repo: WorkflowRepository 实例
        """
        self._svc = skills_service
        self._repo = repo

    # ─── 主入口 ───

    def convert_workflow_to_skill(self, wf_id: str,
                                  *, force: bool = False) -> Dict[str, Any]:
        """把指定 workflow 转换为 Skill

        Args:
            wf_id: 工作流ID
            force: 是否跳过质量门控（用于人工强制触发）

        Returns:
            {workflow_id, skill_id, skill_name, version, action}
            action: created | already_converted

        Raises:
            WorkflowConvertError: 工作流不存在 / 未通过质量门控 / 转换失败
        """
        with traced_action("workflow_convert_to_skill",
                           workflow_id=wf_id, force=force):
            t0 = time.time()
            wf = self._repo.get(wf_id)
            if not wf:
                raise WorkflowConvertError(
                    f"工作流不存在: {wf_id}",
                    code="NOT_FOUND", workflow_id=wf_id,
                )

            # 幂等：已转换过则返回已存在的 skill_id
            if wf.converted_to_skill_id and not force:
                existing = self._svc.get(wf.converted_to_skill_id)
                if existing:
                    logger.info(json.dumps({
                        "trace_id": "",
                        "module_name": "skill_converter",
                        "action": "convert.idempotent",
                        "workflow_id": wf_id,
                        "skill_id": wf.converted_to_skill_id,
                        "duration_ms": 0,
                        "level": "INFO",
                    }, ensure_ascii=False))
                    return {
                        "workflow_id": wf_id,
                        "skill_id": wf.converted_to_skill_id,
                        "skill_name": existing.name,
                        "version": existing.version,
                        "action": "already_converted",
                    }

            # 质量门控
            if not force:
                self._check_quality_gate(wf)

            # 生成 skill 数据
            skill_data = self._generate_skill_data(wf)

            # 落库（防冲突：若 skill_id 已存在则加后缀）
            skill_data = self._resolve_id_conflict(skill_data)

            try:
                skill = self._svc.create_manual(skill_data)
            except Exception as e:
                raise WorkflowConvertError(
                    f"创建技能失败: {e}",
                    code="CREATE_FAILED", workflow_id=wf_id,
                ) from e

            # 回写 converted_to_skill_id
            wf.converted_to_skill_id = skill.id
            wf.touch()
            self._repo.upsert(wf)

            duration_ms = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "skill_converter",
                "action": "convert.ok",
                "workflow_id": wf_id,
                "skill_id": skill.id,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO",
            }, ensure_ascii=False))
            emit_metric("yunshu_workflow_convert_total",
                        labels={"success": "true"}, kind="counter")
            track_event("workflow_converted_to_skill", {
                "workflow_id": wf_id,
                "skill_id": skill.id,
                "success_count": wf.success_count,
                "confidence": wf.confidence,
            })

            return {
                "workflow_id": wf_id,
                "skill_id": skill.id,
                "skill_name": skill.name,
                "version": skill.version,
                "action": "created",
            }

    # ─── 质量门控 ───

    def _check_quality_gate(self, wf: LearnedWorkflow) -> None:
        """检查 workflow 是否达到转换门控"""
        reasons: List[str] = []

        if wf.status != WorkflowStatus.ACTIVE.value:
            reasons.append(f"状态为 {wf.status}（需 ACTIVE）")
        if not wf.enabled:
            reasons.append("工作流未启用")
        if wf.success_count < MIN_SUCCESS_COUNT:
            reasons.append(
                f"success_count={wf.success_count} < {MIN_SUCCESS_COUNT}"
            )
        if wf.confidence < MIN_CONFIDENCE:
            reasons.append(
                f"confidence={wf.confidence:.2f} < {MIN_CONFIDENCE}"
            )
        if wf.priority < MIN_PRIORITY:
            reasons.append(
                f"priority={wf.priority} < {MIN_PRIORITY}"
            )

        if reasons:
            raise WorkflowConvertError(
                f"工作流 {wf.id} 未通过质量门控: {'; '.join(reasons)}",
                code="QUALITY_GATE_FAILED",
                workflow_id=wf.id,
            )

    # ─── 生成 skill 数据 ───

    def _generate_skill_data(self, wf: LearnedWorkflow) -> Dict[str, Any]:
        """把 LearnedWorkflow 编译为 SkillsMgmtService.create_manual 所需字典"""
        skill_id = self._derive_skill_id(wf.id)
        skill_name = wf.name[:200] if wf.name else f"由工作流 {wf.id} 转换"
        description = wf.description or f"自动从工作流 {wf.id} 抽象而来"
        content = self._compile_skill_content(wf)

        return {
            "id": skill_id,
            "name": skill_name,
            "description": description,
            "content": content,
            "content_type": "markdown",
            "category": "custom",
            "tags": list(set([*wf.tags, "from_workflow", "auto_converted"])),
            "author": "skill_converter",
            "source": "workflow_learning",
            "source_url": "",
            "default_params": {
                "workflow_id": wf.id,
                "trigger_patterns": wf.trigger_patterns,
            },
            "dependencies": list({
                step.tool_name for step in wf.steps if step.tool_name
            }),
        }

    def _derive_skill_id(self, wf_id: str) -> str:
        """workflow_id → skill_id（保留语义，加前缀避免冲突）"""
        # 把 wf-xxx / workflow-xxx 形式转换为 wf-xxx-skill
        safe_id = re.sub(r"[^a-z0-9_\-]", "-", wf_id.lower())
        return f"{safe_id}-skill"

    def _resolve_id_conflict(self, skill_data: Dict[str, Any]) -> Dict[str, Any]:
        """若 skill_id 已存在，加数字后缀避免冲突"""
        sid = skill_data["id"]
        if not self._skill_exists(sid):
            return skill_data

        # 已存在 → 加 -2, -3 ...
        for n in range(2, 100):
            new_id = f"{sid}-{n}"
            if not self._skill_exists(new_id):
                skill_data["id"] = new_id
                return skill_data

        # 极端情况：用时间戳
        skill_data["id"] = f"{sid}-{int(time.time())}"
        return skill_data

    def _skill_exists(self, skill_id: str) -> bool:
        """检查技能是否存在（兼容 SkillsMgmtService.get 抛异常的语义）"""
        try:
            self._svc.get(skill_id)
            return True
        except Exception:
            return False

    def _compile_skill_content(self, wf: LearnedWorkflow) -> str:
        """编译 SKILL.md 正文内容（markdown）

        包含:
            - 描述与触发条件
            - 步骤清单（每步的工具、参数模板、条件）
            - 预期输出特征
            - 来源会话信息
        """
        lines: List[str] = []
        lines.append(f"# {wf.name}")
        lines.append("")
        if wf.description:
            lines.append(wf.description)
            lines.append("")

        lines.append("## 触发条件")
        if wf.trigger_patterns:
            for p in wf.trigger_patterns:
                lines.append(f"- `{p}`")
        else:
            lines.append(f"- 任务签名: `{wf.task_signature}`")
        lines.append("")

        lines.append("## 步骤清单")
        if not wf.steps:
            lines.append("(无步骤)")
        else:
            for i, step in enumerate(wf.steps):
                lines.append(f"### 步骤 {i + 1}: {step.tool_name}")
                if step.description:
                    lines.append(f"> {step.description}")
                if step.params_template:
                    lines.append("**参数模板:**")
                    lines.append("```json")
                    lines.append(json.dumps(step.params_template,
                                            ensure_ascii=False, indent=2))
                    lines.append("```")
                if step.output_key:
                    lines.append(f"**输出键:** `{step.output_key}`")
                if step.condition:
                    lines.append(f"**执行条件:** `{step.condition}`")
                lines.append(f"**超时:** {step.timeout_ms}ms")
                lines.append("")
        lines.append("")

        if wf.expected_output_pattern:
            lines.append("## 预期输出特征")
            lines.append(f"```regex\n{wf.expected_output_pattern}\n```")
            lines.append("")

        lines.append("## 来源")
        lines.append(f"- workflow_id: `{wf.id}`")
        lines.append(f"- success_count: {wf.success_count}")
        lines.append(f"- failure_count: {wf.failure_count}")
        lines.append(f"- confidence: {wf.confidence:.2f}")
        if wf.source_session_id:
            lines.append(f"- source_session_id: `{wf.source_session_id}`")
        if wf.source_user_input:
            lines.append(f"- source_user_input: `{wf.source_user_input[:100]}`")

        return "\n".join(lines)

    # ─── 外部技能翻译（LLM 驱动） ───

    def convert_external_skill(self, external_data: Dict[str, Any],
                               llm_client=None,
                               *, target_id: str = "") -> Dict[str, Any]:
        """把其他 agent 的技能描述翻译为云枢 SKILL 格式并注册

        支持的输入格式:
            - {name, description, steps/prompt, ...} 任意 JSON
            - 兼容 OpenAI GPTs / Claude Skills / MCP tools 等格式

        Args:
            external_data: 外部技能的原始描述（dict）
            llm_client: 可选的 LLM 客户端（None 时走规则转换）
            target_id: 指定目标 skill_id（空则自动派生）

        Returns:
            {skill_id, skill_name, source_format, action: "created"}

        Raises:
            WorkflowConvertError: 翻译或注册失败
        """
        with traced_action("convert_external_skill",
                           target_id=target_id,
                           has_llm=bool(llm_client)):
            t0 = time.time()
            source_format = external_data.get("source_format", "unknown")

            if llm_client is not None:
                skill_data = self._llm_translate(external_data, llm_client)
            else:
                skill_data = self._rule_translate(external_data)

            if target_id:
                skill_data["id"] = target_id
            else:
                skill_data["id"] = self._derive_external_id(
                    external_data.get("name", "external"),
                    skill_data.get("id", ""),
                )

            skill_data = self._resolve_id_conflict(skill_data)
            skill_data.setdefault("source", "external_agent")
            skill_data.setdefault("category", "custom")

            try:
                skill = self._svc.create_manual(skill_data)
            except Exception as e:
                raise WorkflowConvertError(
                    f"注册外部技能失败: {e}",
                    code="CREATE_FAILED",
                ) from e

            duration_ms = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "skill_converter",
                "action": "convert_external.ok",
                "skill_id": skill.id,
                "source_format": source_format,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO",
            }, ensure_ascii=False))
            emit_metric("yunshu_external_skill_convert_total",
                        labels={"source_format": source_format},
                        kind="counter")
            track_event("external_skill_converted", {
                "skill_id": skill.id,
                "source_format": source_format,
                "used_llm": llm_client is not None,
            })

            return {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "source_format": source_format,
                "action": "created",
            }

    def _llm_translate(self, external_data: Dict[str, Any],
                       llm_client) -> Dict[str, Any]:
        """调用 LLM 翻译外部技能为云枢格式

        实现: 构造翻译 prompt，调用 llm_client，解析 JSON 响应
        兜底: LLM 失败时降级到 _rule_translate
        """
        prompt = self._build_translation_prompt(external_data)
        try:
            # llm_client 需要遵循项目通用 LLM 接口
            # 假设支持 chat(prompt) -> str 或 invoke(prompt) -> str
            response = None
            for method in ("chat", "invoke", "complete", "generate"):
                if hasattr(llm_client, method):
                    response = getattr(llm_client, method)(prompt)
                    break
            if response is None:
                raise WorkflowConvertError(
                    "llm_client 不支持已知调用方法 (chat/invoke/complete/generate)",
                    code="LLM_API_UNSUPPORTED",
                )
            # 尝试从响应中解析 JSON
            parsed = self._extract_json_from_response(response)
            if parsed:
                return parsed
            # 解析失败 → 降级规则
            logger.warning("LLM 响应无法解析为 JSON，降级规则转换")
            return self._rule_translate(external_data)
        except WorkflowConvertError:
            raise
        except Exception as e:
            logger.warning(f"LLM 翻译失败，降级规则转换: {e}")
            return self._rule_translate(external_data)

    def _build_translation_prompt(self, external_data: Dict[str, Any]) -> str:
        """构造 LLM 翻译 prompt"""
        return (
            "你是技能格式转换器。请把以下外部 agent 的技能描述转换为云枢 SKILL 格式。\n"
            "返回严格的 JSON，字段如下：\n"
            "- id: kebab_case 标识符\n"
            "- name: 显示名\n"
            "- description: 简短描述\n"
            "- content: Markdown 格式的完整 SKILL.md 正文（含步骤说明）\n"
            "- tags: List[str]\n"
            "- dependencies: List[str]（依赖的工具名）\n\n"
            f"外部技能数据:\n```json\n"
            f"{json.dumps(external_data, ensure_ascii=False, indent=2)}\n```"
        )

    @staticmethod
    def _extract_json_from_response(response: Any) -> Optional[Dict[str, Any]]:
        """从 LLM 响应中提取 JSON"""
        if isinstance(response, dict):
            return response
        text = str(response)
        # 找第一个 { 与最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    def _rule_translate(self, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """规则式翻译：把外部技能描述映射为云枢格式（无 LLM 时的兜底）"""
        name = external_data.get("name") or external_data.get("title") or "external-skill"
        description = external_data.get("description", "")
        steps = external_data.get("steps") or external_data.get("actions") or []
        prompt = external_data.get("prompt") or external_data.get("instructions") or ""

        # 编译 content
        lines = [f"# {name}", ""]
        if description:
            lines += [description, ""]
        if prompt:
            lines += ["## 使用说明", prompt, ""]
        if steps:
            lines += ["## 步骤", ""]
            if isinstance(steps, list):
                for i, s in enumerate(steps):
                    if isinstance(s, dict):
                        tool = s.get("tool") or s.get("name") or f"step-{i + 1}"
                        params = s.get("params") or s.get("parameters") or {}
                        lines.append(f"{i + 1}. **{tool}**: `{json.dumps(params, ensure_ascii=False)}`")
                    else:
                        lines.append(f"{i + 1}. {s}")
            elif isinstance(steps, dict):
                for k, v in steps.items():
                    lines.append(f"- **{k}**: `{json.dumps(v, ensure_ascii=False)}`")
            lines.append("")

        content = "\n".join(lines)
        skill_id = self._derive_external_id(name, "")

        return {
            "id": skill_id,
            "name": name[:200],
            "description": description[:500] if description else f"外部技能 {name}",
            "content": content,
            "content_type": "markdown",
            "tags": ["external", "imported"],
            "dependencies": list({
                s.get("tool") or s.get("name")
                for s in (steps if isinstance(steps, list) else [])
                if isinstance(s, dict) and (s.get("tool") or s.get("name"))
            }),
        }

    def _derive_external_id(self, name: str, fallback: str = "") -> str:
        """从外部技能名派生 skill_id

        优先级: fallback (LLM 返回的 id) > name > 默认值
        """
        # 优先使用 fallback（如 LLM 返回的 id）
        base = fallback or name or "external-skill"
        safe = re.sub(r"[^a-z0-9_\-]", "-", str(base).lower())
        safe = re.sub(r"-+", "-", safe).strip("-")
        if not safe:
            safe = "external-skill"
        if not safe[0].isalpha():
            safe = f"ext-{safe}"
        return safe[:60]
