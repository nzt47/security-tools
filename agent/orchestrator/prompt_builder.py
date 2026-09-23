"""PromptBuilder — System Prompt 构建与上下文组装

职责：
- 从 Memory 构建历史上下文摘要
- 从 Working Memory 构建工作记忆文本
- 格式化 System Prompt 模板（含 Token 预算检查与截断）
- 组装上下文消息（budget_context / fallback）
- **提示词片段合并**：把"有拥有者的片段"（role=line/task/...）合并成模板尾部文本

分离自 orchestrator.py _call_llm / _call_llm_v2 的 Prompt 构建部分。
不影响 _build_tool_status_text() 等混入方法（仍在 Orchestrator 上）。

【预算关系（务必读，别制造两套预算）】
    系统提示词的**预算权威只有一个**：build_system_prompt 里既有的 10000-token
    检查（超限 ⇒ tool 段截到 300 字符 + skill 段置空；_call_llm 里同样一份）。
    因此本模块调 compose_fragments 时**一律不传 budget_tokens**（=不做二次裁剪）。
    compose_fragments 的预算裁剪是给"自带预算的调用方"用的能力（见
    agent/prompt_manager/roles.py），提示词主链路刻意不用它 ——
    否则两套预算会在同一段文本上互相打架（一个丢片段、一个截字段），
    超限时到底少了什么将无法解释。
    唯一例外是"硬片段"：预算超限走旧截断时，role=line 这类不可裁剪片段仍保留
    （见 system_tail_text(hard_only=True)），因为丢掉它会让"本线自称的身份"
    与真实能力边界不一致。
"""
import json
import logging
from datetime import datetime
from typing import Iterable, Optional

from agent.logging_utils import log_dict
from agent.prompt_manager.roles import (
    PromptFragment,
    compose_fragments,
)

logger = logging.getLogger(__name__)

# Few-shot 每工具渲染上限(防御性;实际采样上限由 ToolFewshotStore.FEWSHOT_PER_TOOL 控制)
_FEWSHOT_RENDER_PER_TOOL = 2

#: 有模板槽位的角色 → 槽位名。没有槽位的角色（system/persona/line/task）
#: 只能追加在模板之后（见 system_tail_text）。
SLOT_ROLES = {
    "tool": "tool_status",
    "skill": "skill_instructions",
    "memory": "memory_context",
}

#: role=line 片段的来源前缀（source="line:<主线 id>"，审计用）
LINE_SOURCE_PREFIX = "line:"


def line_fragment_for_profile(profile, *, line_id: Optional[str] = None):
    """把**一条主线档案**折成 role="line" 片段（纯函数：不读盘、不抛异常）。

    这是"某条线会注入什么"的**唯一口径**：运行时装配（line_prompt_fragment）
    与 HTTP 投影（/api/agent-lines/preview、/validate 的 prompt_fragments）
    都走这里，避免前端或路由层自己再判一遍导致两份口径分叉。

    Args:
        profile: LineProfile（或其鸭子类型替身）；None ⇒ 无片段。
        line_id: 兜底主线 id（档案自身没有 id 时用于拼 source）。

    Returns:
        PromptFragment(role="line", source="line:<id>") 或 **None**：
            - profile 为 None；
            - 已停用（enabled=False，= 未装线）；
            - prompt_note 为空 / 全空白。
    """
    if profile is None:
        return None
    if not getattr(profile, "enabled", False):
        return None
    note = (getattr(profile, "prompt_note", "") or "").strip()
    if not note:
        return None
    lid = str(getattr(profile, "id", "") or line_id or "")
    return PromptFragment(
        role="line",
        content=note,
        source="%s%s" % (LINE_SOURCE_PREFIX, lid),
    )


def line_prompt_fragment(line_id: Optional[str] = None):
    """取"生效主线"的 prompt_note，包成 role="line" 的提示词片段。

    数据来源：agent.lines（只读、**惰性 import**，不在模块级建立依赖边）——
    resolve_line_id() 解析生效主线，get_line_registry().load(id) 读档案。

    Args:
        line_id: 显式主线 id；None ⇒ 用 resolve_line_id()（显式 > 激活指针 > None）。

    Returns:
        PromptFragment(role="line", source="line:<id>")；下列任一情形返回 **None**：
            - 未装线（没有激活指针且没显式指定）；
            - 档案不存在 / 已停用（enabled=False）；
            - prompt_note 为空或全空白；
            - 读档案抛异常（LineRegistryError / YAML 损坏 / 任何异常）。
        返回 None 时调用方**必须**保持与改动前逐字一致的旧行为 ——
        这正是"未装线等于没改过"的接线纪律。

    绝不抛异常、绝不打断对话：本函数是所有失败模式的收口点。
    """
    try:
        from agent.lines import get_line_registry, resolve_line_id

        lid = line_id or resolve_line_id()
        if not lid:
            return None
        profile = get_line_registry().load(lid)
        if profile is None:
            return None
        if not getattr(profile, "enabled", False):
            # 停用线 = 未装线（与 assemble_for_line 同一口径：停用主线不参与装配）
            logger.info(log_dict({
                'module_name': 'prompt_builder',
                'action': 'prompt_builder.line_prompt_fragment.disabled',
                'line_id': lid,
                'message': '[主线] 主线 %s 已停用，prompt_note 片段不注入' % (lid,),
            }))
        return line_fragment_for_profile(profile, line_id=lid)
    except Exception as e:  # noqa: BLE001  取档案失败绝不影响对话
        logger.warning(log_dict({
            'module_name': 'prompt_builder',
            'action': 'prompt_builder.line_prompt_fragment.degraded',
            'error': '%s: %s' % (type(e).__name__, e),
            'message': '[主线] prompt_note 取用失败，按未装线处理（主链路零影响）: %s' % (e,),
        }))
        return None


def task_fragment(content, source: str):
    """构造 role="task" 片段（当轮素材）；内容不是非空字符串 ⇒ 返回 None。

    为什么要有这个小包装：调用点在 system prompt 的构建路径上，
    **任何异常都会打断对话**。这里把"类型不对 / 全空白"两种情况在入口就判掉，
    返回值可能是 None，调用方用 `if f is not None` 过滤即可。

    Args:
        content: 素材文本（非 str 或全空白 ⇒ None）。
        source: 人读来源（如 "context_assembler" / "workflow_material"）。

    Returns:
        PromptFragment(role="task") 或 None。
    """
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        return PromptFragment(role="task", content=content, source=source)
    except Exception as e:  # noqa: BLE001 构造失败不得打断对话
        logger.warning(log_dict({
            'module_name': 'prompt_builder',
            'action': 'prompt_builder.task_fragment.degraded',
            'source': source,
            'error': '%s: %s' % (type(e).__name__, e),
            'message': 'task 片段构造失败（跳过该片段）: %s' % (e,),
        }))
        return None


def system_tail_text(fragments: Iterable[PromptFragment], *,
                     hard_only: bool = False) -> str:
    """把"没有模板槽位"的片段合并成追加在模板之后的尾部文本。

    无槽位角色 = 所有不在 :data:`SLOT_ROLES` 里的角色（system/persona/line/task）。
    槽位角色（tool/skill/memory）由 build_system_prompt 填进模板对应槽位，
    **不会**出现在尾部 —— 否则同一段文本会被注入两次。

    Args:
        fragments: 片段集合（None/空 ⇒ 返回 ""，调用方据此保持旧行为）。
        hard_only: True 时只保留不可裁剪片段（role ∈ HARD_ROLES），
            供"预算超限走旧截断"的路径使用（旧截断会丢弃可裁剪的当轮素材，
            但保留主线段落）。默认 False = 全部无槽位片段。

    Returns:
        合并后的尾部文本；空 ⇒ ""（调用方不要追加多余分隔符）。

    降级：本函数对**传错的元素**也是宽容的（取不到 role 就按"无槽位"对待），
    并在 merge 抛错时兜底为按**传入顺序**用 "\n\n" 拼接 —— 提示词构建失败
    绝不能打断对话，兜底结果与本文件改动前的裸拼逐字一致。
    """
    selected = []
    for f in (fragments or ()):
        role = getattr(f, "role", None)
        if role in SLOT_ROLES:
            continue  # 有槽位的角色由模板承载，不再追加到尾部（否则注入两次）
        if hard_only and not getattr(f, "hard", False):
            continue
        selected.append(f)
    if not selected:
        return ""
    try:
        return compose_fragments(selected).text
    except Exception as e:  # noqa: BLE001
        logger.warning(log_dict({
            'module_name': 'prompt_builder',
            'action': 'prompt_builder.system_tail_text.degraded',
            'error': '%s: %s' % (type(e).__name__, e),
            'message': '片段合并失败，降级为按传入顺序拼接: %s' % (e,),
        }))
        texts = [str(getattr(f, "content", f)) for f in selected]
        return "\n\n".join(t for t in texts if t.strip())


def build_fewshot_message(fewshot_samples: dict) -> dict | None:
    """把采样的 few-shot 样本组装为 system 消息(注入 messages 动态区,user_input 之前)。

    Args:
        fewshot_samples: {tool_name: [{input, output}, ...]},来自 ToolFewshotStore.sample_for_tools

    Returns:
        {"role": "system", "content": ...};无样本或异常 → None(调用方跳过注入)
    """
    if not fewshot_samples or not isinstance(fewshot_samples, dict):
        return None
    try:
        examples = []
        for tool_name, samples in fewshot_samples.items():
            for s in (samples or [])[:_FEWSHOT_RENDER_PER_TOOL]:
                if not isinstance(s, dict):
                    continue
                in_data = s.get("input", {}) if isinstance(s.get("input"), dict) else {}
                examples.append({
                    "tool": tool_name,
                    "input": in_data,
                    "output": s.get("output", {}),
                    "extracted_params": list(in_data.keys()),
                    "missing_params": [],
                })
        if not examples:
            return None
        content = (
            "以下是当前可用工具过往成功调用的脱敏示例,仅供参数提取参考,"
            "不要直接复用其中的具体值:\n"
            + json.dumps({"examples": examples}, ensure_ascii=False, indent=2)
        )
        return {"role": "system", "content": content}
    except Exception as e:
        logger.warning("[PromptBuilder] build_fewshot_message 降级返回 None: %s", e)
        return None


class PromptBuilder:
    """System Prompt 构建器

    Usage:
        builder = PromptBuilder(
            token_counter=memory._token_counter,
            memory_token_limit=token_limit,
        )
        sysp, wm = builder.build_system_prompt(...)
        msgs = builder.build_context_messages(...)
    """

    def __init__(self, token_counter=None, memory_token_limit: int = 8000):
        """
        Args:
            token_counter: 可调用对象 count(text) -> int（如 memory._token_counter）
            memory_token_limit: 上下文 Token 上限
        """
        self._token_counter = token_counter
        self._memory_token_limit = memory_token_limit

    # ════════════════════════════════════════════════════════════════
    #  记忆上下文摘要
    # ════════════════════════════════════════════════════════════════

    def build_memory_context(self, memory, max_summary_len: int = 300) -> str:
        """从 Memory 构建历史上下文摘要

        优先使用已保存的摘要 summary_data，回退到最近 2 条消息。

        Args:
            memory: Memory 对象（需有 load_summary / get_context 方法）
            max_summary_len: 摘要最大字符数

        Returns:
            格式化后的上下文文本
        """
        memory_context = ""
        try:
            summary_data = memory.load_summary()
            if summary_data and summary_data[0]:
                memory_context = summary_data[0][:max_summary_len]
            else:
                context_messages = memory.get_context(token_limit=5000)
                if context_messages:
                    recent = context_messages[-2:]
                    lines = []
                    for m in recent:
                        if m.get('content'):
                            lines.append("{role}: {content}".format(
                                role=m['role'], content=m['content'][:100]))
                    memory_context = " | ".join(lines)
        except Exception:
            pass
        if not memory_context:
            memory_context = "（暂无历史对话）"
        return memory_context

    # ════════════════════════════════════════════════════════════════
    #  工作记忆文本
    # ════════════════════════════════════════════════════════════════

    def build_working_memory_text(self, memory, max_len: int = 200) -> str:
        """从 Working Memory 构建简短工作记忆文本

        Args:
            memory: Memory 对象（需有 get_working_memory 方法）
            max_len: 最大总字符数

        Returns:
            格式化后的工作记忆文本（含前缀 "\n[工作中] "）
        """
        try:
            wm = memory.get_working_memory()
            if wm:
                items = []
                for k, v in wm.items():
                    if k == "interaction_count":
                        continue
                    if isinstance(v, list):
                        items.append("{key}: {val}".format(
                            key=k, val='; '.join(str(x)[:60] for x in v[-3:])))
                    else:
                        items.append("{key}: {val}".format(key=k, val=str(v)[:80]))
                if items:
                    combined = " | ".join(items)
                    if len(combined) > max_len:
                        combined = combined[:max_len] + "..."
                    return "\n[工作中] " + combined
        except Exception:
            pass
        return ""

    # ════════════════════════════════════════════════════════════════
    #  System Prompt 构建
    # ════════════════════════════════════════════════════════════════

    def build_system_prompt(
        self,
        body_status: str,
        tool_status: str,
        skill_instructions: str,
        profile,
        memory_context: str = "",
        wm_text: str = "",
        get_template_fn=None,
        fragments: Optional[Iterable[PromptFragment]] = None,
    ) -> str:
        """格式化 System Prompt（含 Token 预算检查与自动截断）

        Args:
            body_status: 身体状态文本
            tool_status: 工具状态文本
            skill_instructions: 技能指令文本
            profile: Behavior Profile 对象（需有 label / description 属性）
            memory_context: 记忆上下文摘要
            wm_text: 工作记忆文本（来自 build_working_memory_text）
            get_template_fn: 获取模板的可调用对象，默认从 agent.digital_life 导入
            fragments: **片段集合**（agent/prompt_manager/roles.py::PromptFragment）。
                None（默认）⇒ 走旧路径，输出与改动前**逐字一致**。
                给了片段 ⇒ 先合并一次，再按角色归位：
                  - 槽位角色（tool/skill/memory，见 SLOT_ROLES）填进模板对应槽位；
                    该角色没有片段时沿用形参传入的文本（增量语义，不强制调用方改写）；
                  - 无槽位角色（system/persona/line/task）合并成尾部文本，
                    追加在模板（与 wm_text）之后，分隔符 "\n\n"。

        模板结构与槽位名**不变**（tests/unit/test_tools_prompt_alignment.py 用真实
        模板断言 "## 核心原则" / "## 记忆线索" / "【工具】" 等标记必须仍在）。

        Returns:
            格式化后的 System Prompt 字符串
        """
        if get_template_fn is None:
            from agent.digital_life import _get_template
            get_template_fn = _get_template

        _sp_template = get_template_fn()

        # 计算必要字段
        current_date = f"{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日"
        mode_name = profile.label
        mode_description = profile.description

        # ── 片段合并（fragments=None ⇒ 这一步不存在，旧路径逐字不变）──
        # 预算：这里**刻意不传** budget_tokens —— 预算权威是下面的 10000-token
        # 检查（单一权威，避免两套预算在同一段文本上互相打架，见模块 docstring）。
        tail_text = ""
        if fragments is not None:
            # 物化一次：下面预算超限分支还要复用同一批片段（生成器只能用一次）
            fragments = tuple(fragments)
            composed = compose_fragments(fragments, counter=self._token_counter)
            tool_status = composed.role_text("tool") or tool_status
            skill_instructions = composed.role_text("skill") or skill_instructions
            memory_context = composed.role_text("memory") or memory_context
            tail_text = system_tail_text(composed.parts)

        system_prompt = _sp_template.format(
            current_date=current_date,
            body_status=body_status,
            mode_name=mode_name,
            mode_description=mode_description,
            memory_context=memory_context,
            tool_status=tool_status,
            skill_instructions=skill_instructions,
        )
        if wm_text:
            system_prompt += wm_text
        if tail_text:
            system_prompt += "\n\n" + tail_text

        # ── Token 预算检查 ──
        if self._token_counter:
            try:
                sp_tokens = self._token_counter.count(system_prompt)
                sp_budget = 10000
                if sp_tokens > sp_budget:
                    logger.warning(
                        "[Token] system prompt %d tokens 超预算 %d，截断工具状态",
                        sp_tokens, sp_budget,
                    )
                    brief_tools = (
                        (tool_status[:300] + "\n...（已截断）")
                        if len(tool_status) > 300
                        else tool_status
                    )
                    system_prompt = _sp_template.format(
                        current_date=current_date,
                        body_status=body_status,
                        mode_name=mode_name,
                        mode_description=mode_description,
                        memory_context=memory_context,
                        tool_status=brief_tools,
                        skill_instructions="",
                    )
                    if wm_text:
                        system_prompt += wm_text
                    if fragments is not None:
                        # 截断口径与旧路径一致：可裁剪的尾部素材（task 等）丢弃、
                        # tool 段截到 300 字符、skill 段置空；但**不可裁剪的硬片段**
                        # （role=line/persona/system）必须活下来 —— 见 system_tail_text。
                        hard_tail = system_tail_text(fragments, hard_only=True)
                        if hard_tail:
                            system_prompt += "\n\n" + hard_tail
                logger.info(
                    "[Token] system prompt: %d tokens (预算 %d)", sp_tokens, sp_budget
                )
            except Exception:
                pass

        return system_prompt

    # ════════════════════════════════════════════════════════════════
    #  上下文消息组装
    # ════════════════════════════════════════════════════════════════

    def build_context_messages(
        self,
        memory,
        tool_calling_service,
        user_input: str,
        last_tool_steps=None,
        token_limit: int = None,
    ) -> list[dict]:
        """组装 LLM 调用的上下文消息列表

        优先使用 memory.get_budget_context()（含 Token 预算分配），
        失败时降级为 memory.get_context()。

        Args:
            memory: Memory 对象
            tool_calling_service: 工具调用服务（决定是否追加工具调用提示）
            user_input: 用户当前输入
            last_tool_steps: 上一步工具调用步骤（可选）
            token_limit: 上下文 Token 上限，默认使用 self._memory_token_limit

        Returns:
            list[dict] — 消息列表（含追加的 user 消息）
        """
        token_limit = token_limit or self._memory_token_limit
        messages = []

        # 固定 system 消息前置（提升 LLM 前缀缓存命中率）
        if tool_calling_service:
            messages.append({
                "role": "system",
                "content": (
                    "⚡ 立即检查：用户这句话需要工具吗？如果需要，直接发起函数调用。"
                    "绝对禁止只发文字描述你将要做的操作。"
                    "没调用工具 = 没执行。立即行动。"
                ),
            })

        # 优先使用 budget_context（Token 预算分配）
        try:
            recent = memory._storage.load_recent_messages(limit=50)
            summary_data = memory.load_summary()
            summary_text = summary_data[0] if summary_data else None

            budget_context = memory.get_budget_context(
                recent_messages=recent,
                summary_text=summary_text,
                tool_results=last_tool_steps or [],
            )
            messages.extend(budget_context)
        except Exception as e:
            logger.warning("Budget context assembly failed: %s, falling back", e)
            try:
                context = memory.get_context(token_limit=token_limit)
                if context:
                    messages.extend(context)
            except Exception:
                pass

        messages.append({"role": "user", "content": user_input})

        logger.debug(log_dict({
            'module_name': 'prompt_builder',
            'action': 'prompt_builder.build_context_messages.prompt_order',
            'message': '[PromptOrder] fixed=[tool_urge@idx0] dynamic=[budget_context@idx1-%d, user_input@idx%d]' % (
                len(messages) - 2, len(messages) - 1
            ),
            'messages_count': len(messages),
            'has_tool_urge': bool(tool_calling_service),
        }))

        return messages

    # ════════════════════════════════════════════════════════════════
    #  V2 上下文消息组装（直接模式）
    # ════════════════════════════════════════════════════════════════

    def build_context_messages_v2(self, memory, user_input: str,
                                  token_limit: int = None) -> list[dict]:
        """V2 模式下的上下文消息组装（不包含 budget_context）

        Args:
            memory: Memory 对象
            user_input: 用户输入
            token_limit: Token 上限

        Returns:
            list[dict] — 消息列表
        """
        messages = []
        try:
            context = memory.get_context(token_limit=token_limit or self._memory_token_limit)
            if context:
                messages.extend(context)
        except Exception:
            pass
        messages.append({"role": "user", "content": user_input})
        return messages
