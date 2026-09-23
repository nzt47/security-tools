"""分身装配单 —— 派一个分身之前，它到底拿到什么

【为什么单独一个模块】
    "子代理拿到哪些工具"此前只存在于**调用方内部**：按主线装配的算法写在
    `agent/tools/fan_out_tools.py::_granted_tools_for_line`（工具模块里），
    而"子代理不含记忆读写"的硬禁写在 `agent/subagent/toolset.py`。
    后果是：任何**新的**分身入口（容器委派、单发 `delegate`、外部通道）都得自己重抄
    一遍这段装配，抄漏一处就会出现"某条路径的分身能力比别的路径宽"，而这种事
    在评审里看不出来（两条路径的代码长得不一样，但都"看起来对"）。

    本模块把它收敛成**一个纯函数 + 一份只读装配单**：

        assembly = resolve_subagent_assembly(line_id, registry=…, meta=…, available=…)

    调用方只做两件事：把装配单塞进委派契约（`DelegationContext.metadata`）、
    把它的说明如实回给上层。**不复制**任何装配算法与权限矩阵。

【分层：一条链上只有一个权威】
    | 层 | 唯一权威 | 本模块 |
    |---|---|---|
    | L1 工具原子（plane/effect/risk/tags） | `data/tool_definitions/*.yaml`（经 `agent.lines.load_tool_meta`） | 只读 |
    | L2 主线档案（权重/保底/效果上限） | `data/agent_lines/*.yaml`（经 `LineRegistry`） | 只读 |
    | 装配算法（保底/打分/截断） | `agent/lines/assembler.py::assemble` | 调用，不重写 |
    | 子代理权限矩阵（§7.0） | `agent.security.actor_matrix`（经 `SubAgentToolset`） | 调用，不重写 |

【两条与主智能体**相反**的失败语义（本模块最关键的两行判断）】
    1. `line_id` 为空（任务未指定 line，且全局激活指针也为空）⇒ 用**只读默认集**
       （`_default_subagent_tools` 去掉 govern 平面、再去掉 §5.7 机制 3 硬禁项）。
       这不是"降级"：**没点名**就按最小集给，而不是按最大集给。
    2. 点名了 line，但档案不存在 / 已停用 / 损坏 ⇒ **抛 `LineUnavailable`**，该任务
       就地失败，**绝不回退成全量授权**。
       【为什么与主智能体侧的"未装线=旧行为"不冲突】主智能体那条纪律管的是
       "**没点名**"（`agent/lines/integration.py`：没装线就等于没改过）；这里是
       "**点了名却装不上**"——把后者也当成"没点名"，等于用一个坏档案把子代理的
       权限面悄悄放到最大，那正是这套治理要防的事。

【后半程：技能面与提示词片段（同一次装配，同一份事实）】
    一个分身除了"能用哪些工具"，还会带着两样东西走：
      1. **技能面**（`skills`/`skills_mode`/`skills_note`）——本线的技能包
         （`agent/lines/skillpack.py::SkillPack`）允许它用哪些技能。技能是**授权面**，
         不是正文：本模块只下发"允许哪些 id"，**绝不**把技能指令内容拼进任何提示词
         （§5.7 机制 1/2；子代理的 system prompt 是云枢自有固定文本，见
         `agent/subagent/executor.py::DELEGATE_SYSTEM_PROMPT`）。
      2. **提示词片段**（`prompt_note`/`prompt_source`）——本线档案的
         `prompt_note`，经提示词角色层（`agent/prompt_manager/roles.py` 的
         `PromptFragment(role="line")` + `compose_fragments`）合成出**正文与来源**。
         发起方拿到的是"一段有出处的文本"，至于它最终进 constraints 还是别处，
         由发起方按自己的契约决定（`fan_out` 进 ②约束；理由见 docs/分身装配单.md）。
    **分身专属收紧**：子代理不含私人记忆读写（§5.7 机制 3），因此本线技能包里
    带**私人记忆/人格/生命轨迹**性质的技能不下发给分身。判据**数据驱动**（看技能标签，
    不看 id 黑名单），标签取不到时**不收紧**并如实记账——详见 `_skill_tag_index` /
    `_tighten_private_skills`。

【依赖纪律】
    全程**函数内惰性导入** `agent.lines` / `agent.subagent.toolset` /
    `agent.tools.subagent_tools`：本模块被工具注册链路间接引用，模块级重依赖会把
    注册期的导入顺序变成隐式契约（既有代码同款处理，见 `fan_out_tools` 的惰性导入）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class LineUnavailable(Exception):
    """主线不可用（不存在 / 已停用 / 档案损坏）⇒ 该任务失败，**不回退成全量授权**"""


@dataclass(frozen=True)
class SubagentAssembly:
    """分身的只读装配单

    Attributes:
        line_id: 生效主线 id；空串 = 未点名（走只读默认集）。
        mode: `"line"`（按主线装配）| `"default-readonly"`（只读默认集）。
        tools: 授权给该分身的工具名（已去 govern、已去 §5.7 机制 3 硬禁项）。
        needs_approval: 其中**需要人工确认**的工具名（由 L1 的 plane/effect/risk 派生）。
        note: 人读的装配说明（如实回给上层；判断依据见模块 docstring）。
        skills: 本线技能包里允许下发给分身的技能 id（**授权面**，不是正文）。
            口径：`SkillPack.filter()` 的结果，再去掉私人记忆/人格/生命轨迹类
            （见 `_tighten_private_skills`）。
        skills_mode: `"unrestricted"`（本线未收紧技能面）| `"whitelist"`（按本线
            `skills:` 声明做白名单）——取值直接来自 `SkillPack.mode`，本模块不另立词表。
        skills_note: 人读的技能面说明（含"未收紧"/"取不到标签"等**事实**；
            判断依据见 `_skills_note`）。
        prompt_note: 本线 `prompt_note` 经提示词角色层合成的**正文**
            （`PromptFragment(role="line")` + `compose_fragments`）；本线没有该片段时为空串。
        prompt_source: 该片段的来源标识（如 `"line:engineering"`）；无片段时为空串。
            发起方据此标注"这段文本从哪来"，不得自行拼一个形似的来源串。
    """

    line_id: str = ""
    mode: str = ""
    tools: Tuple[str, ...] = ()
    needs_approval: Tuple[str, ...] = ()
    note: str = ""
    skills: Tuple[str, ...] = ()
    skills_mode: str = ""
    skills_note: str = ""
    prompt_note: str = ""
    prompt_source: str = ""

    def __len__(self) -> int:
        return len(self.tools)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "mode": self.mode,
            "tools": list(self.tools),
            "needs_approval": list(self.needs_approval),
            "note": self.note,
            "skills": list(self.skills),
            "skills_mode": self.skills_mode,
            "skills_note": self.skills_note,
            "prompt_note": self.prompt_note,
            "prompt_source": self.prompt_source,
        }


def _drop_hard_denied(granted: Sequence[str]) -> Tuple[List[str], List[str]]:
    """剔除 §5.7 机制 3 硬禁项（记忆读写等），保持原顺序

    返回 `(保留, 被剔除)`。为什么在**授权前**就剔：`SubAgentToolset` 的判定是
    「申请 ∩ 授权 − 矩阵拒绝」，硬禁项若留在授权集里，子代理会拿到一份**永远调不动**
    的空头授权；而一旦它真去调用，`DelegationExecutor` 会判**整次委派失败**
    （`E_TOOL_NOT_AUTHORIZED`）。口径与执行层同源（同一个 `SubAgentToolset.hard_denied`），
    本模块不另立标准。
    """
    from agent.subagent.toolset import SubAgentToolset

    denied = set(SubAgentToolset.hard_denied(granted))
    return ([t for t in granted if t not in denied],
            [t for t in granted if t in denied])


# ════════════════════════════════════════════════════════════
#  技能面（本线技能包 → 下发给分身的技能 id）
# ════════════════════════════════════════════════════════════

#: 私人记忆 / 人格 / 生命轨迹性质的技能标签词表（**精确匹配**，不是子串匹配）
#:
#: 【为什么用标签而不是 id 黑名单】写死 id 列表等于把"哪些技能属于私人记忆"这件事
#: 钉在代码里：技能改名/新增/被蒸馏出来时没人会记得回来改它，而**漏掉的后果是
#: 一个私人记忆技能被下发给分身**（宁漏不误的那种错，恰恰是这里不能接受的）。
#: 标签是技能自己的声明（data/skills_repo/<id>/skill.md 的 front matter），
#: 新增技能时由声明者顺手写对，判定随之生效。
_PRIVATE_SKILL_TAGS: Tuple[str, ...] = (
    "memory", "persona", "lifetrace", "life_trace",
    "记忆", "人格", "生命轨迹",
)

#: 共享知识库标签：带这些标签的技能**不**算私人记忆（见 _tighten_private_skills）
_SHARED_KNOWLEDGE_TAGS: Tuple[str, ...] = ("knowledge",)


def _skill_tag_index() -> Optional[Dict[str, Tuple[str, ...]]]:
    """技能 id → 标签（**唯一权威**：SkillLoader.list_all_metadata()，即文件轨 front matter）

    【为什么是它（三个候选读完后的取舍）】
        data/skills_repo/<id>/skill.md 的 front matter 是**人工声明**技能语义标签的
        地方——本仓库真正带"记忆/人格"语义的技能（memory_summary / self_reflection /
        context_aware / voice_interaction …）全在这里；而
        SkillLoader.list_all_metadata() 正是它的官方读取入口：自带索引缓存、逐技能
        解析失败只跳过该技能（不会因为一个坏档案就整份读不出来）。本模块因此
        **不自己解析 front matter**——那会造出第二份解析口径，迟早与技能子系统分叉。
      - 不选 data/skills_mgmt.json（主轨）：主轨标签是 SkillsMgmtService._derive_tags
        从 content_type/category **自动派生**的检索词，不是人工语义声明；直接读 JSON
        又绕开了 store 的解析/迁移口径。拿派生词当安全判据，等于把"检索得像不像"
        当成"是不是私人记忆"——那是臆断。
      - 不选"两份都读"：那就是两处口径，正是本模块要消灭的东西。
    【覆盖面的**如实**交代】该来源只覆盖文件轨：主轨独有技能（如
      engineering-test-delivery）拿不到标签。本模块对此的处理是**不收紧 + 记账**
      （见 _tighten_private_skills 与 _skills_note 的"取不到标签"），而不是猜。

    Returns:
        {skill_id: (tag, …)}；**None = 标签来源不可用**（取不到标签 ⇒ 一律不收紧）。
        **不做 memo**：授权面判定必须基于当下事实；调用点已按主线逐条缓存装配单
        （fan_out_tools._run_fan_out 的 line_cache），重复读盘的代价可忽略。
    """
    try:
        # 惰性 import：本模块不在 import 期拉 skills_mgmt（工具注册链路会间接引用本模块）
        from agent.skills_mgmt.loader import SkillLoader

        rows = SkillLoader().list_all_metadata()
    except Exception as e:  # noqa: BLE001 目录不可用不得让委派挂掉（按不收紧处理）
        logger.warning("[assembly] 技能标签来源不可用（按不收紧处理）: %s", e)
        return None
    if not rows:
        # 空结果 = 拿不到任何标签（而不是"所有技能都没标签"）：同样按不可用记账，
        # 免得把"读不出来"说成"查过了，没有"。
        logger.warning("[assembly] 技能标签来源返回空（按不收紧处理）")
        return None
    index: Dict[str, Tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        sid = str(row.get("skill_id") or row.get("id") or "").strip()
        if not sid:
            continue
        raw = row.get("tags")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple, set, frozenset)):
            # 形状不认识 ⇒ 记成"这个 id 没有可用标签"（空元组），由调用方按
            # "取不到标签"处理；**不猜**它的语义。
            index[sid] = ()
            continue
        index[sid] = tuple(str(t).strip().lower() for t in raw if str(t).strip())
    return index


def _tighten_private_skills(
    candidates: Sequence[str],
    tag_index: Optional[Mapping[str, Tuple[str, ...]]],
) -> Tuple[List[str], List[str], List[str]]:
    """剔除私人记忆/人格/生命轨迹类技能，返回 (保留, 被剔除, 取不到标签)

    【判据（数据驱动，不看 id）】标签 ∩ _PRIVATE_SKILL_TAGS 非空
    **且** 标签 ∩ _SHARED_KNOWLEDGE_TAGS 为空。

    后半个条件与 agent/subagent/toolset.py::_derive_tool_operation_rules 的
    「memory 且非 knowledge」边界**同源**：kb_* 那类**共享知识库**技能不是
    §5.7 机制 3 所指的私人记忆——机制 3 要防的是子代理读写**父体的私人记忆**；
    把共享知识库一并剔除会直接掐掉 knowledge 线子代理的检索/入库能力，
    那是过度收紧，不是最小暴露。

    【取不到标签 ⇒ 不收紧】tag_index 为 None（来源不可用），或该 id 不在索引里
    （空标签同样算"取不到"），都归入第三个返回值：**保留**该技能并在 skills_note
    里如实写明。理由：判据是"声明出来的事实"，声明取不到时"默认收紧"会变成静默的
    能力剥夺（使用者只看到"某个技能莫名其妙不生效"）；"默认不收紧"至少是**可审计**的
    ——记账里点名了是哪几项、为什么没判。
    """
    kept: List[str] = []
    dropped: List[str] = []
    untagged: List[str] = []
    if tag_index is None:
        return list(candidates), dropped, list(candidates)
    private = set(_PRIVATE_SKILL_TAGS)
    shared = set(_SHARED_KNOWLEDGE_TAGS)
    for sid in candidates:
        raw = tag_index.get(sid)
        if not raw:
            untagged.append(sid)
            kept.append(sid)
            continue
        tags = set(raw)
        if (tags & private) and not (tags & shared):
            dropped.append(sid)
            continue
        kept.append(sid)
    return kept, dropped, untagged


def _skills_note(
    pack: Any,
    candidates: Sequence[str],
    dropped: Sequence[str],
    untagged: Sequence[str],
    tags_available: bool,
) -> str:
    """技能面的人读说明（如实记账：模式/来源/剔除了谁/哪几项取不到标签）

    "模式来源"的人读文案一律取上游 SOURCE_LABELS（agent/lines/skillpack.py）
    —— 本模块不另写一套中文说明，免得界面/日志/返回信封各说各话。
    """
    from agent.lines import MODE_WHITELIST, SOURCE_LABELS

    source = str(getattr(pack, "source", "") or "")
    label = SOURCE_LABELS.get(source, source)
    line_id = str(getattr(pack, "line_id", "") or "")
    whitelist = str(getattr(pack, "mode", "")) == MODE_WHITELIST
    if whitelist:
        head = f"技能包（line:{line_id}）：{label}（本线允许 {len(candidates)} 项）"
    else:
        head = f"技能包：{label}；未收紧（技能面 = 运行时技能目录 {len(candidates)} 项）"
    parts = [head]
    if dropped:
        parts.append(
            f"剔除 {len(dropped)} 个私人记忆/人格/生命轨迹类技能"
            f"（§5.7 机制 3 同源理由：子代理不含私人记忆读写）：{list(dropped)}")
    elif tags_available:
        parts.append("无可判定的私人记忆/人格类技能需要剔除")
    if not tags_available:
        parts.append(
            f"未收紧（取不到标签）：技能标签来源不可用 ⇒ 一个都不剔除（{len(untagged)} 项）")
    elif untagged:
        parts.append(
            f"另有 {len(untagged)} 项未收紧（取不到标签）：{list(untagged)}"
            if whitelist else f"其中 {len(untagged)} 项未收紧（取不到标签）")
    return "；".join(parts)


def _resolve_skills(pack: Any) -> Tuple[Tuple[str, ...], str, str]:
    """技能包 → (技能 id 元组, 模式, 人读说明)

    【候选池的取法】白名单模式取 pack.allowed（上游已按 YAML 声明顺序去重 ∩ known），
    不收紧模式取运行时技能目录 known_skill_ids()（排序后再过 filter：目录视图是集合，
    不排序就没有可复现的顺序）。两条都**经 SkillPack.filter()** 归一，
    本模块不自己判"是否允许"。
    """
    from agent.lines import known_skill_ids

    if bool(getattr(pack, "unrestricted", False)):
        candidates = [str(i) for i in pack.filter(sorted(known_skill_ids()))]
    else:
        candidates = [str(i) for i in pack.filter(list(getattr(pack, "allowed", ()) or ()))]
    tag_index = _skill_tag_index()
    kept, dropped, untagged = _tighten_private_skills(candidates, tag_index)
    note = _skills_note(pack, candidates, dropped, untagged, tag_index is not None)
    return tuple(kept), str(getattr(pack, "mode", "") or ""), note


# ════════════════════════════════════════════════════════════
#  提示词片段（本线 prompt_note → 正文 + 来源）
# ════════════════════════════════════════════════════════════


def _line_prompt_fragment(line_id: str, raw: str) -> Tuple[str, str]:
    """本线 prompt_note → (正文, 来源)（经提示词角色层，**不自己拼片段**）

    【为什么过 PromptFragment / compose_fragments】role="line" 是"本线片段"在提示词
    角色词表（agent/prompt_manager/roles.py::PROMPT_ROLES）里的唯一身份，line 还是
    **不可裁剪**的硬角色（HARD_ROLES）。把原文直接透传 = 绕开角色层，于是"这段文本
    归谁、能不能被裁、为什么没进最终提示词"三问答不上来（那正是角色层要解决的事）。
    本模块只**合成**，不决定它最终进哪里——那是发起方的契约（fan_out 进 ②约束，
    理由见 docs/分身装配单.md）。

    【空片段】原文为空/纯空白 ⇒ compose_fragments 会把它丢进 dropped（reason=empty）
    ⇒ 本函数返回 ("", "")：**没有片段就没有片段**，调用方据此保持旧行为逐字不变
    （不追加任何东西、prompt_source 留空，而不是给出一个指向空内容的来源）。
    """
    source = f"line:{line_id}" if line_id else ""
    text = str(raw or "")
    if not text.strip():
        return "", ""
    try:
        from agent.prompt_manager.roles import PromptFragment, compose_fragments

        composed = compose_fragments(
            [PromptFragment(role="line", content=text, source=source)])
    except Exception as e:  # noqa: BLE001 角色层不可用不得让委派挂掉
        # 降级**不造第二份口径**：来源串本来就是我方传进去的那一个（line:<id>），
        # 正文就是档案原文；只是少了角色层的排序/裁剪参与。如实记一笔。
        logger.warning("[assembly] 提示词角色层不可用（回退到原文 + 来源）: %s", e)
        return text, source
    sources = composed.sources()
    if not sources:
        return "", ""
    return composed.text, str(sources[0])


def resolve_subagent_assembly(
    line_id: str,
    registry: Any,
    meta: Mapping[str, Any],
    available: Sequence[str],
) -> SubagentAssembly:
    """按主线装配一个分身的工具集（fail-closed；唯二出口见模块 docstring）

    Args:
        line_id: 生效主线 id；空串 = 未点名 ⇒ 只读默认集。
        registry: `agent.lines.LineRegistry`。
        meta: `agent.lines.load_tool_meta()` 的产物（plane/effect/risk 判定的唯一数据源）。
        available: 宿主**真实注册**的工具名（候选池）。

    Returns:
        `SubagentAssembly`。

    Raises:
        LineUnavailable: 点名的主线不存在/已停用/档案损坏（该任务失败；
            **绝不**回退成全量授权）。
    """
    from agent.lines import assemble, resolve_skill_pack

    if not line_id:
        from agent.tools.subagent_tools import _default_subagent_tools

        base = [t for t in _default_subagent_tools() if t in meta]
        granted = [t for t in base if meta[t].plane != "govern"]
        granted, _dropped = _drop_hard_denied(granted)
        # 【为什么这里用 resolve_skill_pack(None) 而不是 line_skill_pack("") 】
        # line_skill_pack 的入参缺省/为空时会去读**全局激活指针**
        # （agent/lines/integration.py::resolve_line_id）；而本分支的语义恰恰是
        # "已确认未装线"（调用方已把指针读空）。再读一次指针就会造出"工具走只读默认集、
        # 技能面却按某条线收紧/放行"的错配。resolve_skill_pack(None) 与
        # line_skill_pack 内部 profile is None 那条分支**同一个函数**，不是第二套口径。
        skills, skills_mode, skills_note = _resolve_skills(resolve_skill_pack(None))
        return SubagentAssembly(
            line_id="",
            mode="default-readonly",
            tools=tuple(granted),
            needs_approval=tuple(t for t in granted if meta[t].needs_approval),
            note="未指定 line 且无全局激活主线 ⇒ 使用只读默认集（写文件/Shell 不在内）",
            skills=skills,
            skills_mode=skills_mode,
            skills_note=skills_note,
            prompt_note="",
            prompt_source="",
        )

    try:
        profile = registry.load(line_id)
    except Exception as e:  # noqa: BLE001  档案损坏同样按「不可用」处理，不回退
        raise LineUnavailable(f"主线档案不可用: {line_id}（{e}）") from e
    if profile is None:
        raise LineUnavailable(f"主线不存在: {line_id}（未回退成全量授权）")
    if not profile.enabled:
        raise LineUnavailable(f"主线已停用: {line_id}（未回退成全量授权）")

    result = assemble(profile, list(available), meta=dict(meta))
    granted = list(result.tools)

    dropped_govern: List[str] = []
    if not profile.allow_govern:
        # 双保险：装配器在 allow_govern=False 时已剔除 govern 权重，此处再按
        # YAML 声明（唯一权威）过滤一次，防「权重表被改」把治理工具漏进来。
        dropped_govern = [t for t in granted if meta[t].plane == "govern"]
        granted = [t for t in granted if meta[t].plane != "govern"]

    # §5.7 机制 3：子代理工具集不含记忆读写——主线档案可以给**主智能体**装配
    # 记忆工具，但派给子代理时必须剔除（矩阵 view.memory / memory.write
    # 对 sub_agent 是 ❌，留在授权集里只会变成"授予了却永远调不动"）。
    granted, dropped_protected = _drop_hard_denied(granted)

    needs = [t for t in granted if meta[t].needs_approval]
    protected_note = (f"；另剔除 {len(dropped_protected)} 个 §5.7 机制 3 硬禁工具"
                      f"（子代理不含记忆读写）：{dropped_protected}") if dropped_protected else ""
    if dropped_govern:
        note = (f"沿主线 {profile.id} 装配：已剔除 {len(dropped_govern)} 个 govern 平面工具"
                f"（{profile.id} 未开启 allow_govern）：{dropped_govern}")
    elif profile.allow_govern and needs:
        note = (f"沿主线 {profile.id} 装配：该线显式 allow_govern=true，"
                f"{len(needs)} 个工具需人工确认：{needs}")
    else:
        note = f"沿主线 {profile.id} 装配（{len(granted)} 个工具）"

    # ── 后半程：技能面 + 提示词片段（与工具集**同一次装配**，同一份档案事实）──
    # 技能包走 line_skill_pack：那是**注入侧**用的同一个入口（agent/lines/skillpack.py
    # 是"哪些技能允许注入"的唯一判定处），于是"主智能体能注入什么"与"分身被允许用
    # 什么"不会各算一份而悄悄分叉。它按纪律**永不抛**（异常 ⇒ unrestricted），
    # 故此处不需要 try。
    from agent.lines import line_skill_pack

    pack = line_skill_pack(str(profile.id))
    skills, skills_mode, skills_note = _resolve_skills(pack)
    prompt_note, prompt_source = _line_prompt_fragment(
        str(profile.id), str(getattr(profile, "prompt_note", "") or ""))

    return SubagentAssembly(
        line_id=str(profile.id),
        mode="line",
        tools=tuple(granted),
        needs_approval=tuple(needs),
        note=note + protected_note,
        skills=skills,
        skills_mode=skills_mode,
        skills_note=skills_note,
        prompt_note=prompt_note,
        prompt_source=prompt_source,
    )


__all__ = ["LineUnavailable", "SubagentAssembly", "resolve_subagent_assembly"]
