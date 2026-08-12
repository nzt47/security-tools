"""ContextAssembler — 上下文工程学习（CEL）框架核心模块

依据 docs/zh/智能体学习机制重构计划/D2D3_API架构替代方案设计.md §3.4。
在模型权重不可变前提下，统一组装三层记忆（工作记忆 / 长期检索 / 程序性）+ 工具白名单，
产出供 LLM 使用的 PromptContext，并支持渲染为可旁路注入 system prompt 的文本。

设计约束（三义）:
- 【不易】本模块不 import 任何业务包（规避包级循环依赖），全部数据源以回调注入
- 【变易】组件缺失/异常 → 对应层降级为空，主链路永不因组装失败而中断
- 【简易】组装 / 预算 / 截断逻辑与 scripts/demo_context_assembler.py 验证版一致

Provider 契约（均为可调用对象）:
- working_memory_fn: () -> list[dict]（消息列表 {"role","content"}），缺省空列表
- long_term_fn:      (task: str) -> list[dict]（片段 {"layer","title","content"}），缺省空列表
- procedural_fn:     (task: str) -> tuple[list[dict], dict|None]
                      （skill 指令列表 {"skill_id","name","instruction"},
                        工作流提示 {"wf_id","tool_sequence","confidence"}），缺省 ([], None)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
# 项目规范：环境变量 > config.yaml > 硬编码默认值。
# CONTEXT_ASSEMBLER_LOG_LEVEL=DEBUG 时输出各层拉取/组装明细，便于观察模式实时排查
_LEVEL = os.environ.get("CONTEXT_ASSEMBLER_LOG_LEVEL", "").strip().upper()
if _LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR"):
    logger.setLevel(getattr(logging, _LEVEL))


def estimate_tokens(text: str) -> int:
    """简易 token 估算：中英混合按字符数 /3（CJK 密集场景偏保守）"""
    return max(1, len(text) // 3)


@dataclass
class PromptContext:
    """组装结果 — 对齐设计文档 PromptContext(system, memories, skills, tools)"""

    task: str
    system_text: str                       # 组装后的完整上下文文本
    memory_sections: List[Dict[str, Any]] = field(default_factory=list)  # 记忆层片段
    skill_instructions: List[Dict[str, Any]] = field(default_factory=list)  # 技能指令
    workflow_hint: Optional[Dict[str, Any]] = None  # 工作流命中
    reflection_notes: List[Dict[str, Any]] = field(default_factory=list)  # 反思经验
    tools: List[str] = field(default_factory=list)   # 工具白名单
    layer_tokens: Dict[str, int] = field(default_factory=dict)  # 各层 token 贡献
    total_tokens: int = 0
    budget: int = 0
    truncated: bool = False

    def summary(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "layers": self.layer_tokens,
            "total_tokens": self.total_tokens,
            "budget": self.budget,
            "truncated": self.truncated,
            "skills_hit": [s.get("skill_id") for s in self.skill_instructions],
            "workflow_hit": self.workflow_hint.get("wf_id") if self.workflow_hint else None,
            "reflections_hit": len(self.reflection_notes),
            "tools": self.tools,
        }


class ContextAssembler:
    """上下文组装器 — 三层记忆统一组装

    Usage:
        assembler = ContextAssembler(
            token_budget=3000,
            working_memory_fn=lambda: memory.get_context(token_limit=8000),
            long_term_fn=lambda task: [...],
            procedural_fn=lambda task: ([...skill...], {...workflow...}),
        )
        ctx = assembler.assemble("帮我解析 PDF")
        text = assembler.render_text(ctx)   # 追加到 system prompt
    """

    def __init__(
        self,
        token_budget: int = 3000,
        *,
        working_memory_fn: Optional[Callable[[], list]] = None,
        long_term_fn: Optional[Callable[[str], list]] = None,
        procedural_fn: Optional[Callable[[str], Tuple[list, Optional[dict]]]] = None,
    ) -> None:
        self._budget = max(64, int(token_budget))
        self._working_memory_fn = working_memory_fn
        self._long_term_fn = long_term_fn
        self._procedural_fn = procedural_fn

    # ── 各层拉取（对应设计文档 §3.3 实例化管线；异常/缺失 → 空层降级）──

    def _pull_working_memory(self) -> List[Dict[str, Any]]:
        if not self._working_memory_fn:
            return []
        try:
            raw = self._working_memory_fn() or []
            if raw and isinstance(raw[0], str):
                text = "\n".join(raw)
            else:
                text = "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}" for m in raw)
            text = text[:500]
            logger.debug("[context_assembler] 工作记忆拉取: 消息 %d 条 → %d 字符", len(raw or []), len(text))
            return [{"layer": "工作记忆", "title": "会话摘要+最近消息",
                     "content": text, "tokens": estimate_tokens(text)}] if text else []
        except Exception as exc:
            logger.debug("[context_assembler] 工作记忆降级为空: %s", exc)
            return []

    def _pull_long_term(self, task: str) -> List[Dict[str, Any]]:
        if not self._long_term_fn:
            return []
        try:
            chunks = self._long_term_fn(task) or []
            out = []
            for c in chunks:
                layer = c.get("layer", "长期检索记忆")
                title = c.get("title", "")
                content = c.get("content", "")
                if not content:
                    continue
                out.append({"layer": layer, "title": title, "content": content,
                            "tokens": estimate_tokens(content)})
            logger.debug("[context_assembler] 长期检索拉取: 片段 %d 条", len(out))
            return out
        except Exception as exc:
            logger.debug("[context_assembler] 长期检索降级为空: %s", exc)
            return []

    def _pull_procedural(self, task: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not self._procedural_fn:
            return [], None
        try:
            skills, wf = self._procedural_fn(task)
            skill_out = []
            for s in (skills or []):
                if s.get("instruction"):
                    skill_out.append({
                        "skill_id": s.get("skill_id"),
                        "name": s.get("name", ""),
                        "instruction": s["instruction"],
                        "tokens": estimate_tokens(s["instruction"]),
                    })
            logger.debug("[context_assembler] 程序性记忆拉取: 技能 %d 条, 工作流 %s",
                         len(skill_out), wf.get("wf_id") if wf else None)
            return skill_out, wf
        except Exception as exc:
            logger.debug("[context_assembler] 程序性记忆降级为空: %s", exc)
            return [], None

    # ── 组装 ──

    def assemble(self, task: str, mode: str = "default") -> PromptContext:
        _t0 = time.perf_counter()
        tools = ["search", "read_file", "write_file"] if mode == "default" else ["*"]

        mem_sections = self._pull_working_memory() + self._pull_long_term(task)
        skills, wf_hint = self._pull_procedural(task)
        reflections = [s for s in mem_sections if s["layer"] == "反思经验"]

        parts = ["你是云枢数字生命体。", "【工作记忆】"]
        parts += [s["content"] for s in mem_sections]
        if reflections:
            parts.append("【反思经验（需遵循）】")
            parts += [f"- {s['content']}" for s in reflections]
        if skills:
            parts.append("【可用技能指令】")
            parts += [s["instruction"] for s in skills]
        if wf_hint:
            parts.append("【工作流提示】工具序列: " + " → ".join(wf_hint.get("tool_sequence", [])))
        parts.append("【可用工具】" + ", ".join(tools))
        system_text = "\n".join(parts)

        layer_tokens = {
            "working_memory": sum(s["tokens"] for s in mem_sections if s["layer"] == "工作记忆"),
            "long_term": sum(s["tokens"] for s in mem_sections if s["layer"] == "长期检索记忆"),
            "reflections": sum(s["tokens"] for s in reflections),
            "skills": sum(s["tokens"] for s in skills),
            "workflow": estimate_tokens(str(wf_hint)) if wf_hint else 0,
        }
        total = estimate_tokens(system_text)
        truncated = total > self._budget
        if truncated:
            # 截断策略：保留工作记忆与技能（守会话连续性），截断 LTM 片段文本
            system_text = system_text[: self._budget * 3]
            total = estimate_tokens(system_text)

        logger.debug("[context_assembler] 组装完成 task=%r 耗时=%.1fms layer_tokens=%s total=%d/budget=%d truncated=%s",
                     task, (time.perf_counter() - _t0) * 1000,
                     {k: v for k, v in layer_tokens.items() if v}, total, self._budget, truncated)

        return PromptContext(
            task=task,
            system_text=system_text,
            memory_sections=mem_sections,
            skill_instructions=skills,
            workflow_hint=wf_hint,
            reflection_notes=reflections,
            tools=tools,
            layer_tokens=layer_tokens,
            total_tokens=total,
            budget=self._budget,
            truncated=truncated,
        )

    def render_text(self, ctx: PromptContext) -> str:
        """渲染为可追加到 system prompt 的旁路注入文本（orchestrator 集成入口）"""
        lines = ["【ContextAssembler 增强上下文】"]
        for s in ctx.memory_sections:
            lines.append(f"[{s['layer']}·{s.get('title') or '片段'}] {s['content']}")
        for r in ctx.reflection_notes:
            lines.append(f"[反思经验·需遵循] {r['content']}")
        for s in ctx.skill_instructions:
            lines.append(f"[技能指令·{s.get('name') or s.get('skill_id')}]\n{s['instruction']}")
        if ctx.workflow_hint:
            lines.append("[工作流提示] " + " → ".join(ctx.workflow_hint.get("tool_sequence", [])))
        lines.append(f"[上下文统计] token={ctx.total_tokens}/budget={ctx.budget} truncated={ctx.truncated}")
        return "\n".join(lines)
