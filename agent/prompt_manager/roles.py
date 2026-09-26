#!/usr/bin/env python3
"""提示词角色（片段"拥有者"）词表 + 片段合并器 —— 纯逻辑，零副作用。

【为什么需要这个模块】
    云枢的系统提示词此前是"**一个模板 + 多处在外部裸拼字符串**"：

        template.format(...)                       # agent/orchestrator/orchestrator.py
        + "\n\n" + _ctx_extra                      # ContextAssembler 旁路注入
        + "\n\n" + extra_material                  # 工作流层工具素材

    拼接顺序由**代码位置**决定，而不是由声明决定。于是三个问题没人答得上：

      1. 谁**拥有**这段文本？（系统？人格？主线？技能？工具？记忆？本轮任务？）
      2. 它**能不能被裁**？（预算超限时先丢谁？）
      3. 它**为什么没进**最终提示词？（被谁丢了？还能不能审计？）

    答不上来的后果很具体：提示词一超预算就"静默少一段"，没有人能从最终文本
    或日志里看出来少的是哪一段、谁丢的。本模块把这三问答成**数据**：
    角色词表（:data:`PROMPT_ROLES`）+ 合并器（:func:`compose_fragments`）。

【纯逻辑约束（不要破坏）】
    - 不读 `config.yaml`，不 import `agent.orchestrator`，不做 IO，不写全局状态；
    - 只依赖标准库（`dataclasses` / `typing`）。
    因此它可以被任意一层安全依赖（编排器、UI 文档镜像、单元测试都行），
    也永远不会因为环境缺配置而在 import 期炸掉。

【角色不是"内容类型"】
    `agent/prompt_manager/storage.py::PromptType` 回答的是"这段提示词**是什么**"
    （system/user/tool/skill/template/chat）；本模块的"角色"回答的是
    "这段提示词**归谁管、谁有权改**"。两者正交，可同时出现在一条记录上
    （见 `registry.py` 的 `metadata["owner"]`）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "CROPPABLE_ROLES",
    "DEFAULT_PRIORITY",
    "HARD_ROLES",
    "PROMPT_ROLES",
    "ROLE_SEMANTICS",
    "ComposedPrompt",
    "DroppedFragment",
    "PromptFragment",
    "compose_fragments",
    "estimate_tokens",
    "is_croppable_role",
    "is_hard_role",
    "is_known_role",
    "role_index",
]


# ══════════════════════════════════════════════════════════════════════
#  ① 角色词表
# ══════════════════════════════════════════════════════════════════════

#: 角色词表（**顺序即默认输出顺序**，也是排序的稳定 tie-breaker）。
#:
#: 排序语义 = 前缀缓存语义：越靠前越稳定（改一次全量缓存失效），
#: 越靠后越易变（逐轮变，放在末尾只损失最小前缀）。因此词表顺序同时是
#: "谁先出现"和"预算超限时谁后丢"的权威依据，见 :func:`compose_fragments`。
PROMPT_ROLES: tuple[str, ...] = (
    "system",
    "persona",
    "line",
    "skill",
    "tool",
    "memory",
    "task",
)

#: 每个角色的语义 + **谁有权写**（"有权写"是纪律，不是技术约束）。
ROLE_SEMANTICS: dict[str, str] = {
    "system": (
        "系统骨架：模板渲染出的云枢本体身份与核心原则。"
        "权威来源 = agent/system_prompt_manager.py + data/system_prompt.txt。"
        "有权写：仓库维护者（走 docs/提示词角色管理.md 的治理流程）。运行时任何调用点"
        "都**无权**改写，只能通过模板槽位填充。"
    ),
    "persona": (
        "数字生命人格层：身份叙述与表达要求。"
        "权威来源 = persona 包（PersonaInjector）+ agent/digital_life_persona.py。"
        "有权写：人格子系统。"
    ),
    "line": (
        "主线片段：本 Agent 的身份边界与交付纪律（LineProfile.prompt_note）。"
        "权威来源 = data/agent_lines/<id>.yaml。"
        "有权写：装线的人（UI「主线管理 → prompt_note」或直接改 YAML）。"
        "**运行时只读**——编排器不生成、不改写它。"
    ),
    "skill": (
        "技能指令：按本轮命中的技能渲染出的操作指引。"
        "权威来源 = agent/skills_mgmt + data/skills/*。"
        "有权写：技能子系统。"
    ),
    "tool": (
        "工具/技能状态行：如实告诉模型它现在看得见哪些工具。"
        "权威来源 = 工具注册表 + 主线装配结果（_build_tool_status_text）。"
        "有权写：工具子系统；编排器只按 allow_tools 决定「暴露 / 不暴露」。"
    ),
    "memory": (
        "记忆线索：历史摘要、长期检索与工作记忆。"
        "权威来源 = memory 层 + agent/context/assembler.py。"
        "有权写：记忆子系统。"
    ),
    "task": (
        "本轮任务素材：工作流层已执行工具的结果、ContextAssembler 旁路注入的当轮上下文。"
        "权威来源 = 编排器当轮调用点。"
        "有权写：编排器（**每轮重建**，不得跨轮缓存）。"
    ),
}

#: 不可裁剪的硬片段（预算超限时**绝不丢**）。
#:
#: 判据不是"重要"，而是"丢了就不再是同一个 Agent"：
#:   - system 丢了 ⇒ 没有本体身份；
#:   - persona 丢了 ⇒ 人格/表达要求断层；
#:   - line 丢了 ⇒ 本线自称的身份边界与真实能力边界不一致（**说一套做一套**）。
HARD_ROLES = frozenset({"system", "persona", "line"})

#: 可裁剪片段（预算超限时按 :func:`compose_fragments` 的次序逐个丢弃）。
#:   - skill/tool/memory 有各自的降级形态（截断到 300 字符 / 置空 / 不注入）；
#:   - task 是本轮素材，丢了只会"这一轮少一点素材"，不会改变 Agent 是谁。
CROPPABLE_ROLES = frozenset(set(PROMPT_ROLES) - HARD_ROLES)

#: 片段默认优先级（数值越小越靠前）。
#: 全部片段都用默认值时，输出顺序 = :data:`PROMPT_ROLES` 词表顺序。
DEFAULT_PRIORITY = 50

#: 无 tokenizer 时的粗估系数：约 4 字符 ≈ 1 token。
#: **只在调用方没给 counter 时用于预算裁剪**；编排链路一律传真 counter
#: （memory._token_counter），所以生产路径不会走到这个估算。
_CHARS_PER_TOKEN = 4


def is_known_role(role: str) -> bool:
    """角色是否在词表内（:data:`PROMPT_ROLES`）。"""
    return role in PROMPT_ROLES


def role_index(role: str) -> int:
    """角色在词表里的序号（越小越靠前）；未知角色排到最后。"""
    try:
        return PROMPT_ROLES.index(role)
    except ValueError:
        return len(PROMPT_ROLES)


def is_hard_role(role: str) -> bool:
    """是否不可裁剪片段（:data:`HARD_ROLES`）。"""
    return role in HARD_ROLES


def is_croppable_role(role: str) -> bool:
    """是否可裁剪片段（:data:`CROPPABLE_ROLES`）。"""
    return role in CROPPABLE_ROLES


def estimate_tokens(text: str) -> int:
    """无 tokenizer 时的确定性粗估（约 4 字符/token，向上取整）。

    刻意保持**确定性**：同一文本永远同值，不依赖环境与随机数，
    这样"预算裁剪"在测试里可复现。真 counter 存在时不要用本函数。
    """
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _count_tokens(
    text: str,
    counter: object | None,
) -> tuple[int, str]:
    """按 counter 计数；返回 (tokens, token_source)。

    counter 是**鸭子类型**（两种形态都在仓储里用，故类型上只标注 object）：
        - 可调用对象 `counter(text) -> int`；
        - 带 `.count(text) -> int` 的对象（如 `memory._token_counter`）。
    传了 counter 但不可用 ⇒ `TypeError`（调用方的编程错误，不静默降级）。
    """
    if counter is None:
        return estimate_tokens(text), "estimate"
    fn = getattr(counter, "count", None)
    if callable(fn):
        return int(fn(text)), "counter"
    if callable(counter):
        return int(counter(text)), "counter"
    raise TypeError(
        f"counter 必须是可调用对象或带 .count(text) 的对象，收到: {type(counter)!r}"
    )


# ══════════════════════════════════════════════════════════════════════
#  ② 片段与合并结果
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PromptFragment:
    """一段有"拥有者"的提示词片段。

    Attributes:
        role: 片段拥有者，必须是 :data:`PROMPT_ROLES` 之一（封闭词表）。
        content: 片段正文。**原样拼接，不 trim**；全空白内容会被
            :func:`compose_fragments` 直接丢弃（进 `dropped`，reason="empty"）。
        priority: 排序键，数值越小越靠前。默认 :data:`DEFAULT_PRIORITY`；
            同优先级时按 role 词表顺序，再按传入顺序（稳定排序）。
        source: **人读**的来源说明，用于审计，如 `"line:engineering"`、
            `"context_assembler"`、`"workflow_material"`。
    """

    role: str
    content: str
    priority: int = DEFAULT_PRIORITY
    source: str = ""

    def __post_init__(self) -> None:
        if not is_known_role(self.role):
            raise ValueError(
                "未知提示词角色 {!r}；词表: {}（要新增角色请改 PROMPT_ROLES 并写清语义）".format(self.role, ", ".join(PROMPT_ROLES))
            )
        if not isinstance(self.content, str):
            raise TypeError(
                f"片段内容必须是字符串，收到 {type(self.content).__name__}"
            )

    @property
    def hard(self) -> bool:
        """是否不可裁剪（见 :data:`HARD_ROLES`）。"""
        return is_hard_role(self.role)

    def describe(self) -> str:
        """一行审计描述：`role=line source=line:engineering chars=42`"""
        return f"role={self.role} source={self.source or '-'} chars={len(self.content)}"


@dataclass(frozen=True)
class DroppedFragment:
    """被丢弃的片段 + **丢弃原因**（不许静默消失）。

    Attributes:
        fragment: 被丢弃的片段本体（内容仍在，便于审计"丢了什么"）。
        reason: `"empty"`（全空白，无内容可注入）或 `"budget"`（token 预算裁剪）。
    """

    fragment: PromptFragment
    reason: str


@dataclass(frozen=True)
class ComposedPrompt:
    """合并结果。

    Attributes:
        text: 最终文本（`separator` 连接保留片段）。
        parts: 进入 text 的片段，**输出顺序**。
        dropped: 被丢弃的片段（含原因），先"空片段（输入序）"后"预算裁剪（丢弃序）"。
        tokens: `text` 的 token 数（真 counter 或粗估，见 `token_source`）。
        overflow: True = 硬片段本身就超预算，已无可裁剪片段，**内容未被裁剪**。
            这是"如实报告"，不是错误：由调用方的预算权威决定怎么办。
        token_source: `"counter"` 或 `"estimate"`。
    """

    text: str
    parts: tuple[PromptFragment, ...] = ()
    dropped: tuple[DroppedFragment, ...] = ()
    tokens: int = 0
    overflow: bool = False
    token_source: str = "estimate"

    def role_text(self, role: str, separator: str = "\n\n") -> str:
        """取某一角色的合并文本（按输出顺序）；该角色无片段时返回空串。"""
        return separator.join(p.content for p in self.parts if p.role == role)

    def sources(self) -> tuple[str, ...]:
        """进入 text 的片段来源清单（审计用）。"""
        return tuple(p.source or p.role for p in self.parts)

    def dropped_descriptions(self) -> tuple[str, ...]:
        """被丢弃片段的审计描述清单。"""
        return tuple(
            f"{d.fragment.describe()} reason={d.reason}" for d in self.dropped
        )


# ══════════════════════════════════════════════════════════════════════
#  ③ 合并器
# ══════════════════════════════════════════════════════════════════════


def compose_fragments(
    fragments: Iterable[PromptFragment],
    *,
    budget_tokens: int | None = None,
    counter: object | None = None,
    separator: str = "\n\n",
) -> ComposedPrompt:
    """把片段集合合并成一段文本 —— **顺序确定、裁剪可解释、丢弃可审计**。

    语义（写死在这里，调用方不要另行发明）：

    1. **空片段直接丢弃**：`content.strip() == ""` 的片段不参与拼接，
       进 `dropped`（reason="empty"）。它不会让输出多出空分隔符。
    2. **排序确定**：按 `(priority, role 词表序号, 传入顺序)` 稳定排序。
       绝不依赖 dict 迭代顺序、绝不依赖 set 顺序。
       需要精确理解"确定"到什么程度（避免过度承诺）：
         - priority 或 role 不同的片段：**打乱输入顺序，输出逐字相同**；
         - (priority, role) 完全相同的多个片段：它们之间按**传入顺序**排，
           这是刻意的（同一角色内"谁先传谁先出"，例如两条 task 素材保持调用
           点的因果顺序）——所以这种集合里打乱输入顺序输出会跟着变，
           不属于"不确定"，而是"契约就是如此"。
       判定标准只有一条：输出**只**由 (priority, role, 传入顺序) 决定。
    3. **预算裁剪**：`budget_tokens is None` ⇒ 不裁剪（调用方自有预算权威）。
       给了预算且超限时，按**输出顺序的逆序**逐个丢弃**可裁剪片段**
       （:data:`CROPPABLE_ROLES`），每丢一个重新计数，直到不超限。
       逆序 = "越靠后越易变越可弃"，与词表顺序同源。
    4. **硬片段绝不裁**（:data:`HARD_ROLES`）：预算仍不够时停止，
       `overflow=True` 如实上报，内容一个字符都不动 ——
       与其把身份段落切一半，不如让调用方知道它该另做决策。
    5. 不做**部分截断**（不切半个片段）：整片段进、整片段出，行为可预测、
       日志可复现。（"截断到 300 字符"这类具体降级形态由调用方在自己的
       预算权威里实现，例如 system prompt 的 10000-token 检查。）

    Args:
        fragments: 片段集合（`Iterable[PromptFragment]`；`None` 视为空）。
        budget_tokens: token 预算；None = 不做预算裁剪。
        counter: token 计数器（可调用 或 带 `.count` 的对象）；None 用粗估。
        separator: 片段之间的连接符，默认 `"\n\n"`（与仓储既有裸拼一致）。

    Returns:
        :class:`ComposedPrompt`

    Raises:
        TypeError: fragments 里有非 :class:`PromptFragment` 元素，或 counter 不可用。
            两者都是调用方的编程错误；运行时链路应保证不触发（见
            `agent/orchestrator/prompt_builder.py::system_tail_text` 的降级包装）。
    """
    ordered: list = []
    dropped: list = []

    for idx, frag in enumerate(fragments or ()):
        if not isinstance(frag, PromptFragment):
            raise TypeError(
                f"片段集合只接受 PromptFragment，第 {idx} 个是 {type(frag).__name__}"
            )
        if not frag.content.strip():
            dropped.append(DroppedFragment(frag, "empty"))
            continue
        ordered.append((idx, frag))

    # 【不易】排序键必须自洽到不依赖任何外部状态：priority → 词表顺序 → 传入顺序。
    # list.sort 是稳定排序，第三项其实冗余，但显式写出来才不依赖"实现恰好稳定"。
    ordered.sort(key=lambda item: (item[1].priority, role_index(item[1].role), item[0]))
    kept = [frag for _, frag in ordered]

    text = separator.join(f.content for f in kept)
    tokens, token_source = _count_tokens(text, counter)
    overflow = False

    if budget_tokens is not None and tokens > budget_tokens:
        for pos in range(len(kept) - 1, -1, -1):
            if tokens <= budget_tokens:
                break
            frag = kept[pos]
            if not is_croppable_role(frag.role):
                continue  # 硬片段：绝不裁
            dropped.append(DroppedFragment(frag, "budget"))
            del kept[pos]
            text = separator.join(f.content for f in kept)
            tokens, token_source = _count_tokens(text, counter)
        overflow = tokens > budget_tokens

    return ComposedPrompt(
        text=text,
        parts=tuple(kept),
        dropped=tuple(dropped),
        tokens=tokens,
        overflow=overflow,
        token_source=token_source,
    )
