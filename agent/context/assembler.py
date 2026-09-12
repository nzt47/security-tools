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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)
# 项目规范：环境变量 > config.yaml > 硬编码默认值。
# CONTEXT_ASSEMBLER_LOG_LEVEL=DEBUG 时输出各层拉取/组装明细，便于观察模式实时排查
_LEVEL = os.environ.get("CONTEXT_ASSEMBLER_LOG_LEVEL", "").strip().upper()
if _LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR"):
    logger.setLevel(getattr(logging, _LEVEL))
    if _LEVEL == "DEBUG":
        # 附加专用 DEBUG handler：宿主进程 root 可能为 INFO（如 app_server basicConfig），
        # 单独 setLevel 会被 handler 级别二次过滤，故本模块自带仅放行 DEBUG 的 StreamHandler。
        # 过滤器排除非 DEBUG 记录，避免 INFO 摘要重复输出
        _DEBUG_HANDLER_NAME = "context_assembler_debug"
        if not any(getattr(h, "name", None) == _DEBUG_HANDLER_NAME for h in logger.handlers):
            _h = logging.StreamHandler()
            _h.name = _DEBUG_HANDLER_NAME
            _h.setLevel(logging.DEBUG)
            _h.addFilter(lambda record: record.levelno == logging.DEBUG)
            _h.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s [context_assembler] %(message)s"))
            logger.addHandler(_h)
            logger.info("CONTEXT_ASSEMBLER_LOG_LEVEL=DEBUG：组装明细日志已启用（专用 handler）")


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
    #: TASK-S4-03 注入防御机制 1：被判定为外来文本、**只能进受沙箱槽位**的段。
    #: 仅 `assemble_guarded()` 会填充；既有 `assemble()` 恒为空列表（零行为变化）。
    sandbox_blocks: List[Dict[str, Any]] = field(default_factory=list)

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
            "sandbox_blocks": len(self.sandbox_blocks),
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

    # ── TASK-S4-03 注入防御机制 1：受守卫的组装路径（**新增，不改既有行为**）──

    #: 视为「可信」的层/来源（不必打 taint）
    TRUSTED_SOURCES = frozenset({"", "trusted", "working_memory", "user", "skill"})

    def assemble_guarded(self, task: str, mode: str = "default", *,
                         ledger: Optional[Any] = None,
                         foreign_layers: Optional[Iterable[str]] = None,
                         ) -> PromptContext:
        """组装并施加**注入防御机制 1**（外来文本禁入 system prompt）

        与 `assemble()` 的关系：**先**调 `assemble()` 得到与原路径逐字一致的基线，
        **再**把判定为外来文本的段从 `system_text` 中摘出、改挂到 `sandbox_blocks`。
        故关掉守卫（不调用本方法）时行为与今天完全一致。

        外来来源判定（**有序**，可被 `foreign_layers` 覆盖）：
            1. 段 dict 自带 `source` 字段 → 用它（取值见
               `agent.guardrails.foreign_taint.ForeignSource`）；
            2. 否则按层名：**长期检索层** → `retrieval`（§5.7 机制 1 明列的"检索结果"）；
            3. 工作记忆 / 技能指令 / 工作流提示 → **trusted**。

        【为什么技能指令按可信处理（显式判断，非遗漏）】§5.7 的"文件内容"指**原始**
        外来文件；而进入程序性层的技能已过 §4.5 的确定性回放沙箱 + 验收门 + shadow
        （S3-01/S3-03 交付），属**云枢自己的**程序性记忆。将其判为外来会与 §4.5 冲突，
        且会把"自有用技能"这件事彻底关掉。该判断已登记在 S4-03 验收报告"判断项"。

        Args:
            task / mode: 同 `assemble()`。
            ledger: 外来文本污点账（缺省进程级账）。
            foreign_layers: 覆盖"哪些层视为外来"的层名集合（如 `{"长期检索记忆"}`）。

        Returns:
            `PromptContext`（`sandbox_blocks` 非空表示有段被摘出）。
        """
        if ledger is None:
            try:
                from agent.guardrails.foreign_taint import get_foreign_taint
                ledger = get_foreign_taint()
            except Exception as exc:  # noqa: BLE001 守卫不可用 → 退回基线（不阻断主链路）
                logger.warning("[context_assembler] 注入防御账不可用，退回基线组装: %s", exc)
                return self.assemble(task, mode=mode)

        try:
            from agent.guardrails.foreign_taint import (
                DEST_SYSTEM_PROMPT, check_text, wrap_untrusted)
        except Exception as exc:  # noqa: BLE001 同上：组件缺失不阻断
            logger.warning("[context_assembler] 注入防御组件不可用，退回基线组装: %s", exc)
            return self.assemble(task, mode=mode)

        ctx = self.assemble(task, mode=mode)
        forced = {str(x) for x in (foreign_layers or ())}

        def _source_of(section: Dict[str, Any]) -> str:
            declared = str(section.get("source") or "").strip().lower()
            if declared:
                return declared
            layer = str(section.get("layer") or "")
            if forced and layer in forced:
                return "retrieval"
            if layer == "长期检索记忆" or layer == "long_term":
                return "retrieval"
            return "trusted"

        kept_sections: List[Dict[str, Any]] = []
        blocks: List[Dict[str, Any]] = []
        for section in ctx.memory_sections:
            source = _source_of(section)
            content = str(section.get("content") or "")
            if source in self.TRUSTED_SOURCES or not content:
                kept_sections.append(section)
                continue
            verdict = check_text(content, destination=DEST_SYSTEM_PROMPT, ledger=ledger,
                                 surface="context_assembler.assemble_guarded")
            if verdict.allowed:
                kept_sections.append(section)
                continue
            block = wrap_untrusted(content, source,
                                   ref=str(section.get("title") or section.get("layer") or ""),
                                   ledger=ledger)
            block["source_label"] = str(block.get("source_label") or source)
            block["blocked_from"] = DEST_SYSTEM_PROMPT
            block["layer"] = str(section.get("layer") or "")
            blocks.append(block)
            logger.info("[context_assembler] 外来段已摘出 system prompt: layer=%s source=%s "
                        "chars=%d", section.get("layer"), source, len(content))

        if not blocks:
            return ctx

        ctx.memory_sections = kept_sections
        ctx.reflection_notes = [s for s in ctx.reflection_notes
                                if any(s is k for k in kept_sections)]
        ctx.sandbox_blocks = blocks
        ctx.system_text = self._rebuild_system_text(ctx, mode=mode)
        ctx.total_tokens = estimate_tokens(ctx.system_text)
        ctx.truncated = ctx.total_tokens > self._budget
        ctx.layer_tokens = {
            **ctx.layer_tokens,
            "sandbox_blocks": sum(estimate_tokens(str(b.get("text") or "")) for b in blocks),
        }
        return ctx

    def _rebuild_system_text(self, ctx: PromptContext, *, mode: str = "default") -> str:
        """按 `assemble()` 的同款结构重建 `system_text`（仅用于受守卫路径）

        【为什么单独一个方法而不是改 `assemble()`】`assemble()` 的文本结构是既有
        行为契约（既有用例与旁路注入按它断言）。守卫路径另起一份，产出同构文本，
        从而"守卫开关只影响哪些段被放进来"，不影响文本结构本身。
        """
        tools = ["search", "read_file", "write_file"] if mode == "default" else ["*"]
        parts = ["你是云枢数字生命体。", "【工作记忆】"]
        parts += [s["content"] for s in ctx.memory_sections]
        if ctx.reflection_notes:
            parts.append("【反思经验（需遵循）】")
            parts += [f"- {s['content']}" for s in ctx.reflection_notes]
        if ctx.skill_instructions:
            parts.append("【可用技能指令】")
            parts += [s["instruction"] for s in ctx.skill_instructions]
        if ctx.workflow_hint:
            parts.append("【工作流提示】工具序列: "
                         + " → ".join(ctx.workflow_hint.get("tool_sequence", [])))
        parts.append("【可用工具】" + ", ".join(tools))
        text = "\n".join(parts)
        if estimate_tokens(text) > self._budget:
            text = text[: self._budget * 3]
        return text

    def render_guarded_text(self, ctx: PromptContext) -> str:
        """渲染受守卫上下文（外来段以 `cp-data` 包裹，**不回灌指令区**）

        与 `render_text()` 的差别：末尾追加 `ctx.sandbox_blocks`，每段前置
        "以下为不可信数据"声明并包在 `cp-data` 标记内（§5.7 机制 1/2 的渲染侧形态）。
        """
        base = self.render_text(ctx)
        if not ctx.sandbox_blocks:
            return base
        try:
            from agent.guardrails.instruction_data import (
                DATA_BLOCK_CLOSE, DATA_BLOCK_OPEN, DATA_BLOCK_PREAMBLE)
        except Exception:  # noqa: BLE001 组件缺失 → 只出基线文本（不阻断）
            return base
        lines = [base]
        for block in ctx.sandbox_blocks:
            lines.append(DATA_BLOCK_PREAMBLE)
            lines.append(DATA_BLOCK_OPEN)
            lines.append(str(block.get("text") or ""))
            lines.append(DATA_BLOCK_CLOSE)
        return "\n".join(lines)
