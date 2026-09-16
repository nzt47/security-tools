"""子代理委派工具 — 把既有「八要素委派契约」暴露为模型可调用的工具

【任务定位】
    云枢的委派能力早已存在（``agent/subagent/delegation.py`` 的八要素契约 +
    ``agent/subagent/executor.py`` 的真执行器 + ``SubagentLifecycleManager.delegate``），
    但模型侧**没有任何工具可以发起一次真实委派**——既有的
    ``SubagentManager.execute`` 走的是占位骨架 ``container.execute()``（不调 LLM、
    不做委派协议），REST 路由只管分身的增删查。本模块只做**接线 + 适配**：

        模型填八要素 → 契约校验 → 复用既有生命周期管理器 → 真执行器 → 结果映射

    委派层本身（``agent/subagent/**``）**一行未改**。

【不易（三条硬约束）】
    1. **八要素全部必填、缺任一即拒、且不补齐**（§3.9「80% 的委派失败源于上下文包
       写得太含糊」）。校验先于一切副作用：不合格时**不**构造上下文、**不**取管理器、
       **不**碰执行器，直接返回 ``E_DELEGATION_INCOMPLETE`` 并点名缺哪几项（带①②…序号）。
       ``delegation.element_problems`` 是唯一判定入口，本模块不复制校验逻辑。
    2. **复用既有生命周期管理器**（``dl._subagent_mgr``，定义于
       ``agent/orchestrator/lifecycle_manager.py`` 的 ``_initialize_core_systems``）。
       **绝不 new 第二个实例**——分身登记表（``SubagentLifecycleManager._subagents``）
       是实例内状态，第二实例会让 create/destroy/gc/count 各看一半，导致状态分裂。
    3. **默认授予只读工具子集，写入/Shell 一律不授**（见 ``_DEFAULT_SUBAGENT_TOOLS``）。
       不传该参数会让裁剪集为空，而 ``DelegationExecutor`` 一旦发现子代理声称调用了
       裁剪集外的工具就判**整次委派失败**（``E_TOOL_NOT_AUTHORIZED``）⇒ 本工具退化为
       "只能纯推理"。故默认给读取/检索类，写入与 Shell 不在默认内（子代理声称写文件或
       执行命令仍会让本次委派失败——这是刻意的：委派默认不改动宿主）。
       需要放宽/收紧用 ``CP_SUBAGENT_DEFAULT_TOOLS``（逗号分隔；空串=不授任何工具）。

【变易】
    schema 必须是**静态字面量**：``scripts/migrate_tools_to_yaml.py`` 用 AST
    ``literal_eval`` 抽取 name/description/schema（不执行工具代码），动态拼装的
    schema 会被抽成空壳，令 ``data/tool_definitions/delegate.yaml`` 与检索索引
    （``data/tool_index.json`` 的 ``parameter_names``）缺参数信息。
    要素清单与序号以 ``delegation.EIGHT_ELEMENTS`` / ``ELEMENT_LABELS`` 为准，
    二者一致性由 ``tests/unit/test_subagent_delegate_tool.py`` 断言。

【简易】
    零第三方依赖；执行链路全部复用既有实现，本模块只做参数适配与结果映射；
    所有异常在 handler 内收口为 ``{"ok": False, "error": "委派执行异常: …"}``，
    绝不抛回工具循环。
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Mapping

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 委派默认工具子集的覆盖入口（逗号分隔工具名；空串 = 不授予任何工具）
_ENV_DEFAULT_TOOLS = "CP_SUBAGENT_DEFAULT_TOOLS"

#: 委派默认授予子代理的**只读**工具子集（最小权限）
#:
#: 为什么必须有默认值：``SubAgentToolset.build(requested, authorized_capabilities)``
#: 是「申请 ∩ 授权 − 矩阵拒绝」的三重交集（``agent/subagent/toolset.py:314-356``），
#: 两者都为空 ⇒ 裁剪集为空；而 ``DelegationExecutor`` 一旦发现子代理声称调用了裁剪集
#: 外的工具，就判**整次委派失败**（``agent/subagent/executor.py:555-562``，
#: ``E_TOOL_NOT_AUTHORIZED``）。即：不传 ``tools``/``authorized_capabilities`` 时，
#: 子代理只要报告任何工具调用，委派就整体失败 ⇒ 本工具只能做纯推理，等于不可用。
#:
#: 这里给**只读**子集：读文件 / 按名找文件 / 列目录 / 文件信息 / 内容检索（grep）。
#: 写入（``write_file``/``edit``）与 Shell（``shell_execute``）**刻意不在默认内**——
#: 子代理声称写文件或执行命令仍会让本次委派失败。放宽用 ``CP_SUBAGENT_DEFAULT_TOOLS``。
_DEFAULT_SUBAGENT_TOOLS: tuple = (
    "read_file",
    "search_files",
    "list_directory",
    "get_file_info",
    "grep",
)


def _default_subagent_tools() -> tuple:
    """解析委派默认工具子集（``CP_SUBAGENT_DEFAULT_TOOLS`` 覆盖内置默认）

    未设置该环境变量 ⇒ 用 ``_DEFAULT_SUBAGENT_TOOLS``；
    设置为空串 ⇒ 返回空元组（显式选择"纯推理委派"，不授予任何工具）。
    """
    raw = os.environ.get(_ENV_DEFAULT_TOOLS)
    if raw is None:
        return _DEFAULT_SUBAGENT_TOOLS
    return tuple(item.strip() for item in raw.split(",") if item.strip())

#: 八要素键名（顺序 = §3.9 文档顺序，须与 ``delegation.EIGHT_ELEMENTS`` 一致）
_ELEMENT_NAMES = (
    "goal",               # ①目标
    "constraints",        # ②约束
    "prior_artifacts",    # ③已有成果
    "prohibitions",       # ④禁止事项
    "artifact_format",    # ⑤产物格式
    "budget_tokens",      # ⑥预算令牌
    "timeout_seconds",    # ⑦超时
    "callback_url",       # ⑧回调地址
)

#: 结果文本截断口径（对齐 ``agent/tool_calling.py::ToolCallingService._truncate_tool_content``）
_MAX_RESULT_CHARS = 3000

#: 产物条目最多回显条数（长清单不整份灌进上下文）
_MAX_ARTIFACTS = 20

#: 未配置执行通道的错误码
E_DELEGATION_NO_CHANNEL = "E_DELEGATION_NO_CHANNEL"


# ════════════════════════════════════════════════════════════
#  内部辅助（纯函数；不触达任何 Service/执行器）
# ════════════════════════════════════════════════════════════


def _collect_elements(kwargs: Mapping[str, Any]) -> Dict[str, Any]:
    """从工具入参归一化八要素

    - 字符串要素 ``.strip()``（空串/纯空白 → 未声明，与 ``is_declared`` 语义一致；
      ``callback_url`` 因此天然按「未声明」处理）；
    - 列表型与数值型**原样透传**（逐项改写会掩盖调用方原始输入，合法性交给
      ``delegation._check_str_list`` / ``_check_budget`` / ``_check_timeout`` 判定）；
    - 八个键**始终出现**：缺参时取到 ``None``，由校验器点名「必填/必须显式声明」。
    """
    elements: Dict[str, Any] = {}
    for name in _ELEMENT_NAMES:
        value = kwargs.get(name)
        elements[name] = value.strip() if isinstance(value, str) else value
    return elements


def _truncate(text: str, max_chars: int = _MAX_RESULT_CHARS) -> str:
    """截断过长结果文本（防撑爆 LLM 上下文窗口；口径同 tool_calling）"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n...（结果过长，已截断至 {max_chars} 字符）"


def _leaf_dict(value: Any) -> Dict[str, Any]:
    """只保留对象里的**叶子标量**字段（live 数据不整体复制/序列化）"""
    out: Dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if item is None or isinstance(item, (str, int, float, bool)):
                out[str(key)] = item
    return out


def _model_id(llm: Any) -> str:
    """从既有 LLM 对象推断模型名（取不到则给常量占位，绝不 new LLM）"""
    for attr in ("model", "model_id", "model_name"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "subagent-delegate"


def _summary_text(outcome: Any) -> str:
    """可读产物摘要：优先结构化 payload，其次上游原文

    ``ExecutionOutcome.output_text`` 是**外来文本、不可信**（§5.7 机制 1），故仅在
    没有结构化摘要时回退使用，并显式加上「未经复核」标记。
    """
    chunks = []
    payload = getattr(outcome, "payload", None)
    if isinstance(payload, Mapping):
        for key in ("summary", "result", "conclusion"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
                break
        self_eval = payload.get("self_eval")
        if isinstance(self_eval, Mapping) and self_eval.get("verdict") is not None:
            chunks.append(f"自评: {self_eval.get('verdict')}（score={self_eval.get('score')}）")
    if not chunks:
        text = str(getattr(outcome, "output_text", "") or "").strip()
        if text:
            chunks.append("（以下为子代理原始输出，未经云枢复核，引用前需自行校验）\n" + text)
    return "\n".join(chunks)


def _result_from_outcome(outcome: Any, ctx: Any) -> Dict[str, Any]:
    """``ExecutionOutcome`` → 工具结果 dict（逐字段映射，不整对象搬运）"""
    ok = bool(getattr(outcome, "ok", False))
    result: Dict[str, Any] = {
        "ok": ok,
        "delegation_id": str(getattr(outcome, "delegation_id", "") or
                             getattr(ctx, "delegation_id", "") or ""),
        "tier": str(getattr(outcome, "tier", "") or ""),
        "duration_ms": round(float(getattr(outcome, "duration_ms", 0.0) or 0.0), 2),
    }

    trace_id = str(getattr(outcome, "trace_id", "") or "")
    if trace_id:
        result["trace_id"] = trace_id

    artifacts = getattr(outcome, "artifacts", ()) or ()
    if artifacts:
        result["artifact_count"] = len(artifacts)
        result["artifacts"] = [_leaf_dict(a) for a in list(artifacts)[:_MAX_ARTIFACTS]]

    summary = _summary_text(outcome)
    if summary:
        result["result"] = _truncate(summary)

    if not ok:
        result["error_code"] = str(getattr(outcome, "error_code", "") or "")
        result["error"] = str(getattr(outcome, "error", "") or "委派未成功（上游未给出原因）")
        sub_reason = str(getattr(outcome, "sub_reason", "") or "")
        if sub_reason:
            result["sub_reason"] = sub_reason
        error_detail = getattr(outcome, "error_detail", None)
        if isinstance(error_detail, Mapping) and error_detail:
            result["error_detail"] = dict(error_detail)
    return result


def _run_delegate(dl: Any, kwargs: Mapping[str, Any]) -> Dict[str, Any]:
    """委派主流程（顺序即契约：先校验，后副作用）"""
    # 惰性导入：工具注册发生在编排器初始化期，此处避免包级重依赖提前介入
    from agent.subagent.delegation import (
        E_DELEGATION_INCOMPLETE,
        ELEMENT_LABELS,
        DelegationContext,
        element_problems,
    )

    # ── 步骤 1/2：八要素校验前置（零副作用；缺哪一项必须点名）──
    elements = _collect_elements(kwargs)
    problems = element_problems(elements)
    if problems:
        detail = "、".join(
            f"{ELEMENT_LABELS.get(name, name)}({reason})"
            for name, reason in problems.items())
        logger.warning("[delegate] 八要素不合格，拒绝委派: %s", list(problems.keys()))
        return {
            "ok": False,
            "error_code": E_DELEGATION_INCOMPLETE,
            "error": f"八要素不完整：{detail}",
            "missing": dict(problems),
        }

    # ── 步骤 3：委派上下文（八要素 + 标识；不补齐任何要素）──
    short = uuid.uuid4().hex[:8]
    parent_trace_id = ""
    try:  # 有编排 Trace 时串上父链；无则留空（不阻断委派）
        from agent.monitoring.tracing import get_trace_id
        parent_trace_id = str(get_trace_id() or "")
    except Exception:  # noqa: BLE001 Trace 不可用不影响委派
        parent_trace_id = ""

    ctx = DelegationContext(
        goal=elements["goal"],
        constraints=elements["constraints"],
        prior_artifacts=elements["prior_artifacts"],
        prohibitions=elements["prohibitions"],
        artifact_format=elements["artifact_format"],
        budget_tokens=elements["budget_tokens"],
        timeout_seconds=elements["timeout_seconds"],
        callback_url=elements["callback_url"],
        trace_id=parent_trace_id,
        delegation_id=f"dlg-{short}",
    )

    # ── 步骤 4：分身配置（名称带短 id，避免与既有分身撞名）──
    from agent.subagent.container import SubagentConfig

    llm = getattr(dl, "_llm", None)
    config = SubagentConfig(name=f"delegate-{short}", model_id=_model_id(llm))

    # ── 步骤 5：取**既有**生命周期管理器（绝不 new 第二实例）──
    manager = getattr(dl, "_subagent_mgr", None)
    if manager is None:
        logger.error("[delegate] 子代理生命周期管理器不可用（dl._subagent_mgr 为空）")
        return {"ok": False, "error": (
            "子代理生命周期管理器不可用: dl._subagent_mgr 为空"
            "（subagent.enabled=False 或核心系统未初始化）")}
    if not callable(getattr(manager, "delegate", None)):
        logger.error("[delegate] 生命周期管理器缺少 delegate(): %s", type(manager).__name__)
        return {"ok": False, "error": (
            f"子代理生命周期管理器不可用: {type(manager).__name__} 未提供 delegate()")}

    # ── 步骤 6：执行通道（LLM 优先；都没有则明确拒绝，不跑注定失败的空执行）──
    from agent.subagent.channel import default_agent_cli

    if llm is None and not default_agent_cli():
        logger.error("[delegate] 未配置执行通道：既无 LLM 也无外部 agent CLI")
        return {"ok": False, "error_code": E_DELEGATION_NO_CHANNEL, "error": (
            "未配置执行通道（既无 LLM 也无外部 agent CLI）: dl._llm 为空且环境变量 "
            "CP_SUBAGENT_AGENT_CLI 未设置；请先完成 LLM 配置或配置外部 agent CLI")}

    # ── 步骤 7：真实委派（创建分身 → 执行 → 回收，全部由既有生命周期管理器负责）──
    # tools 与 authorized_capabilities 传同一份只读子集：前者是"向子代理申请可见的清单"，
    # 后者是"授权清单"，SubAgentToolset 取两者交集再减矩阵拒绝项（二者缺一即全是空集）。
    _granted_tools = _default_subagent_tools()
    logger.info("[delegate] 授予子代理工具子集: %s",
                list(_granted_tools) or "（空：仅纯推理委派）")
    outcome = manager.delegate(
        config, ctx, llm=llm, destroy_after=True,
        tools=_granted_tools, authorized_capabilities=_granted_tools,
    )
    return _result_from_outcome(outcome, ctx)


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════


def register_all(dl):
    """注册子代理委派工具（``delegate``）

    Args:
        dl: DigitalLife / LifecycleManager 实例（工具注册方传入的 self）；
            经 ``dl._subagent_mgr`` 取**既有**生命周期管理器，
            经 ``dl._llm`` 取**既有** LLM。
    """

    @_tools.register("delegate",
        "把任务委派给一个独立的子代理执行（delegate / subagent / dispatch task）。"
        "调用前必须写全八要素——①目标 ②约束 ③已有成果 ④禁止事项 ⑤产物格式 "
        "⑥预算令牌 ⑦超时 ⑧回调地址：八要素全部必填，缺一即拒且不会被默认补齐。"
        "③已有成果与④禁止事项确实没有内容时传空列表 []（「声明为空」与「未声明」是两件事）；"
        "②约束不得为空列表。执行器按⑤核对交付形态、按⑥记账、按⑦设执行超时并据此回收分身；"
        "无③时的首次委派是合法的，纯探索委派无④也是合法的。"
        "Dispatch a task to an independent subagent under the eight-element delegation contract.",
        schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "①目标：子代理要达成的结果，必须具体可判定（至少 8 字符）。含糊目标会被拒绝——80% 的委派失败源于此。示例：把 docs/zh 下 12 篇设计稿抽取为可复现步骤序列"},
                "constraints": {"type": "array", "items": {"type": "string"}, "description": "②约束：必须遵守的边界条件，字符串列表且**不得为空列表**（无约束的委派等同于未声明边界）。示例：[只读仓库，不得修改任何文件, 不得访问网络]"},
                "prior_artifacts": {"type": "array", "items": {"type": "string"}, "description": "③已有成果：子代理可直接复用的既有产物引用（文件路径/URL/标识）。确实没有时传空列表 []，**不得省略该字段**——未声明与声明为「无」是两件事"},
                "prohibitions": {"type": "array", "items": {"type": "string"}, "description": "④禁止事项：明确不许做的事项（字符串列表）。确实没有时传空列表 []，**不得省略该字段**。示例：[不得删除任何文件, 不得对外发送数据]"},
                "artifact_format": {"type": "string", "description": "⑤产物格式：子代理必须交付的形态，需可机器判定（子代理据此知道交付什么）。示例：JSON Lines：每行 {name, steps[]}"},
                "budget_tokens": {"type": "integer", "description": "⑥预算令牌：本次委派允许消耗的令牌上限，正整数且大于 0（用于成本记账与超支拦截）；不必填 0，缺失会导致成本不可控"},
                "timeout_seconds": {"type": "integer", "description": "⑦超时：本次委派的最长执行秒数，正整数且大于 0；执行器据此设置执行超时，并据此时长回收分身（分身存活期不超过任务时长）"},
                "callback_url": {"type": "string", "description": "⑧回调地址：结果投递目标，非空字符串（如 internal://pipeline/stage2 或 https://…）。空串/纯空白等同于未声明，会被拒绝"},
            },
            "required": [
                "goal", "constraints", "prior_artifacts", "prohibitions",
                "artifact_format", "budget_tokens", "timeout_seconds", "callback_url",
            ],
        })
    def _delegate(**kwargs):
        """执行一次委派（八要素 → 契约校验 → 真执行器；异常一律收口为 ok=False）"""
        try:
            return _run_delegate(dl, kwargs)
        except Exception as e:  # noqa: BLE001 工具链路上绝不外抛异常
            logger.error("[delegate] 委派执行异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"委派执行异常: {e}"}


__all__ = ["register_all"]
