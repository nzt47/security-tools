"""上下文注入器 — 将三层内容按需注入 LLM 上下文

文章核心价值:
    使用 Agent Skill 的核心价值在于，它将 AI 应用开发从单纯写提示词的"泛而不精"，
    转变为指挥一个配备了全套工具和标准化手册的"专业技工"。
    它通过文件系统实现了业务知识的永久留存，并利用分层按需加载和脚本执行，
    弥补了纯文本提示词在处理文件和运算方面的短板。

本模块实现:
    - inject_metadata(matches): 第一层 — 将匹配技能的元数据注入系统提示词
    - inject_instruction(skill_id): 第二层 — 按需注入完整使用说明
    - inject_result(execution_result): 第三层 — 注入脚本执行结果
    - build_context(intent, max_tokens): 一站式构建上下文（三层联动）

token 预算管理:
    - 第一层: 每技能约 100 token，可一次注入多个
    - 第二层: 按需加载，只注入当前需要的技能
    - 第三层: 只注入执行结果（JSON），不注入代码

设计原则:
    - 后端权威原则: 注入内容由后端决定，前端不推导
    - 按需加载: 只在需要时注入对应层
    - 可观测: 输出结构化日志（trace_id, module_name, action, duration_ms, layer, tokens）
    - token 预算: 注入前检查预算，超预算时降级
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from .loader import SkillLoader, MatchResult, SkillMatch, estimate_tokens
from .executor import ExecutionResult
from .file_store import SkillFileStore
from .observability import logger, emit_metric
from .exceptions import SkillNotFoundError


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════
#  上下文注入器
# ════════════════════════════════════════════════════════════

# 默认 token 预算
_DEFAULT_META_BUDGET = 2000    # 第一层预算（可容纳约 20 个技能元数据）
_DEFAULT_INSTR_BUDGET = 4000   # 第二层预算（单个技能使用说明）
_DEFAULT_RESULT_BUDGET = 2000  # 第三层预算（执行结果）


class ContextInjector:
    """上下文注入器 — 三层按需注入 LLM 上下文

    用法:
        injector = ContextInjector()
        # 第一层：匹配并注入元数据
        ctx = injector.build_context("帮我解析PDF文件", max_tokens=6000)
        # ctx 包含: 系统提示词片段 + 匹配技能 + (可选)使用说明 + (可选)执行结果
    """

    def __init__(self, loader: Optional[SkillLoader] = None,
                 *, meta_budget: int = _DEFAULT_META_BUDGET,
                 instr_budget: int = _DEFAULT_INSTR_BUDGET,
                 result_budget: int = _DEFAULT_RESULT_BUDGET):
        self.loader = loader or SkillLoader()
        self.meta_budget = meta_budget
        self.instr_budget = instr_budget
        self.result_budget = result_budget

    # ──────────────────────────────────────────────
    #  第一层：元数据注入
    # ──────────────────────────────────────────────

    def inject_metadata(self, matches: List[SkillMatch]) -> Dict[str, Any]:
        """第一层 — 将匹配技能的元数据注入系统提示词

        只注入元数据（约 100 token/技能），不注入使用说明或代码。
        用于让 LLM 知道有哪些技能可用。

        Returns: {prompt, matches, estimated_tokens, layer}
        """
        t0 = time.time()
        tid = _trace_id()

        lines = ["## 可用技能（元数据层）"]
        lines.append("以下技能已匹配您的需求，如需使用请告知技能ID：\n")

        total_tokens = 0
        injected = []
        for m in matches:
            if total_tokens + m.estimated_tokens > self.meta_budget:
                # 超预算，停止注入
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "context_injector",
                    "action": "inject_metadata.budget_exceeded",
                    "skill_id": m.skill_id,
                    "budget": self.meta_budget,
                    "used": total_tokens,
                }, ensure_ascii=False))
                break

            lines.append(f"### {m.name} (`{m.skill_id}`)")
            lines.append(f"- 描述: {m.description}")
            if m.tags:
                lines.append(f"- 标签: {', '.join(m.tags)}")
            lines.append(f"- 版本: {m.version}")
            lines.append(f"- 匹配度: {m.score:.2%}")
            lines.append("")

            total_tokens += m.estimated_tokens
            injected.append(m.to_dict())

        prompt = "\n".join(lines)
        elapsed = (time.time() - t0) * 1000

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "context_injector",
            "action": "inject_metadata.layer1.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 1,
            "injected_count": len(injected),
            "estimated_tokens": total_tokens,
            "budget": self.meta_budget,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_inject_tokens",
                    value=total_tokens, kind="histogram",
                    labels={"layer": "1"})

        return {
            "prompt": prompt,
            "matches": injected,
            "estimated_tokens": total_tokens,
            "layer": 1,
        }

    # ──────────────────────────────────────────────
    #  第二层：使用说明注入
    # ──────────────────────────────────────────────

    def inject_instruction(self, skill_id: str) -> Dict[str, Any]:
        """第二层 — 按需注入技能的完整使用说明

        只在 LLM 决定使用某技能后才调用。
        Returns: {prompt, skill_id, estimated_tokens, layer}
        """
        t0 = time.time()
        tid = _trace_id()

        instr_data = self.loader.load_instruction(skill_id)
        instruction = instr_data["instruction"]
        est_tokens = instr_data["estimated_tokens"]

        # 预算检查
        if est_tokens > self.instr_budget:
            # 降级：截断使用说明
            truncated = True
            # 按 token 预算截断（粗略按字符比例）
            ratio = self.instr_budget / est_tokens
            cut_chars = int(len(instruction) * ratio)
            instruction = instruction[:cut_chars] + "\n\n...(使用说明已截断，完整内容请查看技能文件)"
            est_tokens = self.instr_budget
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "context_injector",
                "action": "inject_instruction.truncated",
                "skill_id": skill_id,
                "budget": self.instr_budget,
                "original_tokens": instr_data["estimated_tokens"],
            }, ensure_ascii=False))
        else:
            truncated = False

        prompt = f"## 技能使用说明：{skill_id}\n\n{instruction}"
        elapsed = (time.time() - t0) * 1000

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "context_injector",
            "action": "inject_instruction.layer2.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 2,
            "skill_id": skill_id,
            "estimated_tokens": est_tokens,
            "truncated": truncated,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_inject_tokens",
                    value=est_tokens, kind="histogram",
                    labels={"layer": "2", "skill_id": skill_id})

        return {
            "prompt": prompt,
            "skill_id": skill_id,
            "estimated_tokens": est_tokens,
            "truncated": truncated,
            "layer": 2,
        }

    # ──────────────────────────────────────────────
    #  第三层：执行结果注入
    # ──────────────────────────────────────────────

    def inject_result(self, result: ExecutionResult,
                      *, request_feedback: bool = False,
                      trace_id: str = "") -> Dict[str, Any]:
        """第三层 — 注入脚本执行结果

        只注入执行结果（JSON），不注入代码。
        代码已在后台执行完毕，只把结果传给模型。

        Args:
            result: 脚本执行结果
            request_feedback: 是否在结果后注入"请对该技能效果评分"的引导
            trace_id: 关联追踪ID（用于反馈落库时绑定）

        Returns: {prompt, skill_id, script_name, estimated_tokens, layer,
                  feedback_request?}
        """
        t0 = time.time()
        tid = trace_id or _trace_id()

        result_dict = result.to_dict()
        result_str = json.dumps(result_dict, ensure_ascii=False, indent=2)
        est_tokens = estimate_tokens(result_str)

        # 预算检查
        if est_tokens > self.result_budget:
            # 降级：截断结果
            truncated = True
            ratio = self.result_budget / est_tokens
            cut_chars = int(len(result_str) * ratio)
            result_str = result_str[:cut_chars] + "\n...(结果已截断)"
            est_tokens = self.result_budget
        else:
            truncated = False

        prompt = f"## 脚本执行结果：{result.skill_id}/{result.script_name}\n\n```json\n{result_str}\n```"

        # 自解释 UI 原则：在执行结果后引导用户对技能效果评分
        # 配合 feedback-skill 绑定，收集到的评分将驱动技能自进化
        feedback_request = None
        if request_feedback:
            feedback_request = {
                "skill_id": result.skill_id,
                "trace_id": tid,
                "prompt_text": (
                    f"\n\n---\n**请对该技能效果评分（1-5 分）**\n"
                    f"- 技能ID: `{result.skill_id}`\n"
                    f"- 追踪ID: `{tid}`\n"
                    f"- 评分方式: 回复 `score:5` 或调用 "
                    f"`POST /api/skills-mgmt/{result.skill_id}/feedback`\n"
                    f"- 评分将用于驱动该技能的参数优化、状态晋升或合并废弃"
                ),
            }
            prompt += feedback_request["prompt_text"]
            est_tokens += estimate_tokens(feedback_request["prompt_text"])

        elapsed = (time.time() - t0) * 1000

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "context_injector",
            "action": "inject_result.layer3.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 3,
            "skill_id": result.skill_id,
            "script_name": result.script_name,
            "estimated_tokens": est_tokens,
            "truncated": truncated,
            "success": result.success,
            "request_feedback": request_feedback,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_inject_tokens",
                    value=est_tokens, kind="histogram",
                    labels={"layer": "3", "skill_id": result.skill_id})

        # 埋点预留：技能执行结果注入后请求反馈
        if request_feedback:
            try:
                from .observability import track_event
                track_event("skill_feedback_requested", {
                    "skill_id": result.skill_id,
                    "trace_id": tid,
                    "success": result.success,
                })
            except Exception:
                pass

        return {
            "prompt": prompt,
            "skill_id": result.skill_id,
            "script_name": result.script_name,
            "estimated_tokens": est_tokens,
            "truncated": truncated,
            "layer": 3,
            "feedback_request": feedback_request,
        }

    # ──────────────────────────────────────────────
    #  一站式上下文构建
    # ──────────────────────────────────────────────

    def build_context(self, intent: str, *,
                      max_tokens: int = 6000,
                      top_k: int = 5,
                      auto_load_instruction: bool = False,
                      skill_id: Optional[str] = None) -> Dict[str, Any]:
        """一站式构建上下文（三层联动）

        流程:
            1. 第一层: match(intent) → 匹配技能元数据
            2. 第二层: 如指定 skill_id 或 auto_load_instruction=True → 加载使用说明
            3. 第三层: （不在此方法执行，需显式调用 execute + inject_result）

        Args:
            intent: 用户意图
            max_tokens: 总 token 预算
            top_k: 第一层最多匹配几个技能
            auto_load_instruction: 是否自动加载最高分技能的使用说明
            skill_id: 指定加载某技能的使用说明

        Returns: {intent, layers: {layer1, layer2?}, total_tokens, prompts}
        """
        t0 = time.time()
        tid = _trace_id()

        prompts = []
        total_tokens = 0

        # 第一层：元数据匹配
        match_result = self.loader.match(intent, top_k=top_k)
        if match_result.matches:
            meta_ctx = self.inject_metadata(match_result.matches)
            prompts.append(meta_ctx["prompt"])
            total_tokens += meta_ctx["estimated_tokens"]

            # 第二层：按需加载使用说明
            target_id = skill_id or (
                match_result.matches[0].skill_id if auto_load_instruction else None
            )
            if target_id and total_tokens < max_tokens:
                try:
                    instr_ctx = self.inject_instruction(target_id)
                    if total_tokens + instr_ctx["estimated_tokens"] <= max_tokens:
                        prompts.append(instr_ctx["prompt"])
                        total_tokens += instr_ctx["estimated_tokens"]
                    else:
                        logger.warning(json.dumps({
                            "trace_id": tid,
                            "module_name": "context_injector",
                            "action": "build_context.skip_instruction",
                            "reason": "budget_exceeded",
                            "used": total_tokens,
                            "needed": instr_ctx["estimated_tokens"],
                            "budget": max_tokens,
                        }, ensure_ascii=False))
                except SkillNotFoundError as e:
                    logger.warning(json.dumps({
                        "trace_id": tid,
                        "module_name": "context_injector",
                        "action": "build_context.instruction_not_found",
                        "skill_id": target_id,
                        "error": str(e),
                    }, ensure_ascii=False))

        elapsed = (time.time() - t0) * 1000
        full_prompt = "\n\n".join(prompts)

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "context_injector",
            "action": "build_context.ok",
            "duration_ms": round(elapsed, 2),
            "intent": intent[:100],
            "total_tokens": total_tokens,
            "budget": max_tokens,
            "match_count": len(match_result.matches),
            "has_instruction": len(prompts) > 1,
        }, ensure_ascii=False))

        return {
            "intent": intent,
            "match_result": match_result.to_dict(),
            "prompt": full_prompt,
            "total_tokens": total_tokens,
            "budget": max_tokens,
            "layers": {
                "layer1_metadata": len(match_result.matches) > 0,
                "layer2_instruction": len(prompts) > 1,
                "layer3_execution": False,  # 需显式调用
            },
            "elapsed_ms": round(elapsed, 2),
        }

    # ──────────────────────────────────────────────
    #  token 预算管理
    # ──────────────────────────────────────────────

    def get_budget_status(self) -> Dict[str, Any]:
        """获取 token 预算配置"""
        return {
            "layer1_metadata_budget": self.meta_budget,
            "layer2_instruction_budget": self.instr_budget,
            "layer3_result_budget": self.result_budget,
            "total_budget": self.meta_budget + self.instr_budget + self.result_budget,
        }
