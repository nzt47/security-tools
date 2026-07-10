"""PromptBuilder — System Prompt 构建与上下文组装

职责：
- 从 Memory 构建历史上下文摘要
- 从 Working Memory 构建工作记忆文本
- 格式化 System Prompt 模板（含 Token 预算检查与截断）
- 组装上下文消息（budget_context / fallback）

分离自 orchestrator.py _call_llm / _call_llm_v2 的 Prompt 构建部分。
不影响 _build_tool_status_text() 等混入方法（仍在 Orchestrator 上）。
"""
import logging
from datetime import datetime

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


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

        Returns:
            格式化后的 System Prompt 字符串
        """
        if get_template_fn is None:
            from agent.digital_life import _get_template
            get_template_fn = _get_template

        _sp_template = get_template_fn()

        # 计算必要字段
        current_date = datetime.now().strftime("%Y年%m月%d日")
        mode_name = profile.label
        mode_description = profile.description

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
