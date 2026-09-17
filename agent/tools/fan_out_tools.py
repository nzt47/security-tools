"""并行多线委派工具（``fan_out``）—— 一次把 N 个任务**同时**派给 N 个子代理

【任务定位】
    ``delegate``（``agent/tools/subagent_tools.py``）是**串行单发**：一次调用只起一个
    子代理，且授予集是固定的只读 5 件套（写文件/Shell 刻意不在内）。而云枢的核心诉求是
    「多 Agent，不同的 Agent 各管一条线」——需要**同时**跑多条线，且每条线的子代理拿到
    的工具集应当是**那条主线档案装配出来的**（``data/agent_lines/*.yaml`` →
    ``agent/lines/assembler.py``）。本模块不新造并发原语，只做**接线 + 按主线装配**：

        tasks[]（每项八要素 + 可选 line）
          → 逐个八要素校验（**先全校验、后副作用**：任一不合格即整体拒绝）
          → 每个任务的 line → LineProfile → assemble() → SubAgentToolset.build()
          → SubagentLifecycleManager.delegate_many(specs, max_concurrency=…)
                （逐任务建分身 → DelegationExecutor.execute_many → 统一回收）
          → 逐任务结果汇总（部分成功如实报告）

【不易（四条硬约束）】
    1. **先全校验、后副作用**：所有任务的八要素先经 ``delegation.element_problems``
       逐个判定；任一项不合格 ⇒ 整体拒绝并点名「第几个任务缺哪一项」，**一个子代理都不起**。
       （校验入口与 ``delegate`` 同源，本模块不复制校验逻辑。）
    2. **授权集只来自主线装配**（申请 ∩ 授权 − §7.0 矩阵拒绝，fail-closed）：
       主线不存在/已停用 ⇒ **该任务失败**并说明原因，**绝不**回退成「给全量工具」。
       这条路是唯一的安全防线——``toolset._TOOL_OPERATION_RULES`` 只认 ``memory.read``
       这类抽象点名，对真实工具名（``write_file``）**不设防**，因此**授权清单本身就是防线**。
       （注意 ``LineRegistry.load`` **不**过滤 ``enabled``：主线"已停用"只能由调用方
       自己按 ``profile.enabled`` 判定——本模块就是这么做的。）
    3. **默认不授予 govern 平面工具**（``ext_install``/``generate_tool``/``connect_mcp``
       等会改变云枢自身能力集）：只有主线档案显式 ``allow_govern: true`` 才放行，且放行项
       一律进入 ``needs_approval`` 标注；未开启该开关的线，装配结果里的 govern 项会被
       再过滤一次（双保险，防装配口径漂移）。
    4. **单个子任务失败不得让整体失败**：结果信封恒为「逐任务 results + 计数」，
       执行器异常也在任务内收口，绝不外抛给工具循环。``ok`` 取「**至少有一个成功**」
       （见主流程里对 ok 语义的说明）。

【变易】
    - ``max_concurrency`` clamp 到 ``[1, DEFAULT_MAX_CONCURRENCY]``（§4.2 并发上限）；
    - 单次任务数上限 ``CP_FAN_OUT_MAX_TASKS``（默认 16）——本工具 risk=high，
      成本与副作用都会放大，故设一道明确的成本防线（越界报错而不是静默截断）；
    - 主管线缺省链：``task.line`` → 全局激活主线（``data/agent_lines/_active.json``）
      → 只读默认集（``subagent_tools._DEFAULT_SUBAGENT_TOOLS``）。
【简易】
    零第三方依赖；并发原语、执行器、工具裁剪、主线装配全部复用既有实现；
    handler 内一切异常收口为 ``ok=False``。

【为什么 per-task 授权走工厂参数（而不是 execute_many 的单值参数）】
    ``DelegationExecutor.execute_many(delegations, *, tools=…, authorized_capabilities=…)``
    的这两个参数是**整批共用**的（它逐条转调 ``self.execute(ctx, tools=…,
    authorized_capabilities=…)``），无法按任务区分。**逐任务**参数走与既有
    ``credentials_for`` 同款的**工厂参数**：``tools_for`` / ``authorized_for``
    （按 ctx 现算，见 ``agent/subagent/executor.py`` 的 ``execute_many``）。
    本模块两个工厂同源 = 该任务那条线的装配集，于是 ``SubAgentToolset.build``
    的「申请 ∩ 授权 − 矩阵拒绝」结果恰好等于该任务自己的授权集。
    另外仍把同一份授权集写进 ``ctx.metadata["authorized_capabilities"]``：
    它会随 task_file 物化给子代理（子代理因此知道自己被允许调用什么），
    也是旧版执行器的兜底路径；**权威来源始终是工厂参数**。

【为什么经由 ``SubagentLifecycleManager.delegate_many`` 而不是直接调 execute_many】
    分身纪律（``max_subagents`` 容量上限、TTL 取契约⑦、名称唯一性、执行后回收）
    属于生命周期管理器；直接调 ``execute_many`` 会绕过它（分身不可见、容量不受控）。
    ``delegate_many`` 是既有 ``delegate`` 的批量版：建分身 → ``execute_many`` → 回收，
    并发原语仍是既有实现，本模块只负责"按主线装配 + 逐任务结果汇总"。
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Mapping, Tuple

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 单次任务数上限的环境变量覆盖入口（正整数；非法值按默认处理）
_ENV_MAX_TASKS = "CP_FAN_OUT_MAX_TASKS"

#: 单次 fan_out 最多派发的任务数（本工具 risk=high：每个任务都是一个真实子代理）
_DEFAULT_MAX_TASKS = 16

#: 并发上限的合法下界（上界取 ``executor.DEFAULT_MAX_CONCURRENCY``）
MIN_CONCURRENCY = 1

#: 任务不是对象时的伪要素键（让它能出现在同一份 problems 里）
_STRUCTURE_KEY = "__task__"

#: 错误码
E_FAN_OUT_NO_TASKS = "E_FAN_OUT_NO_TASKS"
E_FAN_OUT_TOO_MANY_TASKS = "E_FAN_OUT_TOO_MANY_TASKS"
E_FAN_OUT_LINE_UNAVAILABLE = "E_FAN_OUT_LINE_UNAVAILABLE"
E_FAN_OUT_BATCH_FAILED = "E_FAN_OUT_BATCH_FAILED"
E_FAN_OUT_NO_RESULT = "E_FAN_OUT_NO_RESULT"


class _LineUnavailable(Exception):
    """主线不可用（不存在 / 已停用 / 档案损坏）⇒ 该任务失败，**不回退成全量授权**"""


# ════════════════════════════════════════════════════════════
#  内部辅助（纯函数 + 只读装配；不产生任何副作用）
# ════════════════════════════════════════════════════════════


def _max_tasks() -> int:
    """单次任务数上限（``CP_FAN_OUT_MAX_TASKS`` 覆盖内置默认）"""
    raw = os.environ.get(_ENV_MAX_TASKS)
    if raw is None or not str(raw).strip():
        return _DEFAULT_MAX_TASKS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("[fan_out] %s=%r 非整数，按默认上限 %d 处理",
                       _ENV_MAX_TASKS, raw, _DEFAULT_MAX_TASKS)
        return _DEFAULT_MAX_TASKS
    if value <= 0:
        logger.warning("[fan_out] %s=%r 非正数，按默认上限 %d 处理",
                       _ENV_MAX_TASKS, raw, _DEFAULT_MAX_TASKS)
        return _DEFAULT_MAX_TASKS
    return value


def _clamp_concurrency(raw: Any) -> Tuple[int, str]:
    """把 ``max_concurrency`` 收敛到合法区间 ``[1, DEFAULT_MAX_CONCURRENCY]``

    Returns:
        (生效并发数, 收敛说明)。说明为空串表示无需收敛。
    """
    from agent.subagent.executor import DEFAULT_MAX_CONCURRENCY

    upper = int(DEFAULT_MAX_CONCURRENCY)
    if raw is None:
        return upper, ""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return upper, (f"max_concurrency 非法（{raw!r} 不是整数），已用默认 {upper}")
    if isinstance(raw, float) and not float(raw).is_integer():
        return upper, (f"max_concurrency 非法（{raw!r} 不是整数），已用默认 {upper}")
    value = int(raw)
    if value < MIN_CONCURRENCY:
        return MIN_CONCURRENCY, (
            f"max_concurrency={value} 越界（下限 {MIN_CONCURRENCY}），已收敛为 {MIN_CONCURRENCY}")
    if value > upper:
        return upper, (
            f"max_concurrency={value} 越界（上限 DEFAULT_MAX_CONCURRENCY={upper}，§4.2），"
            f"已收敛为 {upper}")
    return value, ""


def _available_tool_names() -> List[str]:
    """宿主**真实注册**的工具名（主线装配的候选池）

    注册表为空 ⇒ 返回空列表：装配结果随之是空集（fail-closed），**不**退回 YAML 全量
    ——「无法证明可用」不等于「可以用」。
    """
    names: List[str] = []
    for item in _tools.list_tools():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        logger.warning("[fan_out] 宿主工具注册表为空 ⇒ 任何主线的装配结果都是空集（fail-closed）")
    return names


def _build_executor(llm: Any) -> Any:
    """构造批量执行器（**注入点**：单测据此替换为 mock，不启真实进程/LLM）"""
    from agent.subagent.executor import build_executor

    return build_executor(llm=llm)


def _format_problems(problems: Mapping[str, str]) -> str:
    """{要素名: 原因} → 可读文案（带①②…序号，缺哪一项一眼可见）"""
    from agent.subagent.delegation import ELEMENT_LABELS

    parts = []
    for name, reason in problems.items():
        if name == _STRUCTURE_KEY:
            parts.append(str(reason))
        else:
            parts.append(f"{ELEMENT_LABELS.get(name, name)}（{reason}）")
    return "、".join(parts)


def _elements_of(task: Any) -> Dict[str, Any]:
    """任务 → 归一化八要素（非对象任务返回 ``{}``，由校验器点名缺失）

    归一化口径与 ``delegate`` 完全同源（``subagent_tools._collect_elements``：
    字符串 strip、列表/数值原样透传、八个键始终出现），因此「未声明」与「声明为空」
    在两个工具里的判定一致。
    """
    from agent.tools.subagent_tools import _collect_elements

    if not isinstance(task, Mapping):
        return {}
    return dict(_collect_elements(task))


def _task_problems(task: Any, elements: Mapping[str, Any]) -> Dict[str, str]:
    """单个任务的八要素不合格项（``{}`` = 合格）"""
    from agent.subagent.delegation import element_problems

    if not isinstance(task, Mapping):
        return {_STRUCTURE_KEY: f"任务必须是对象（含八要素），实际为 {type(task).__name__}"}
    return element_problems(elements)


def _resolve_line(line_id: str, registry: Any) -> str:
    """解析任务生效主线：显式 ``line`` 优先，其次全局激活指针；都没有 → ``""``"""
    if line_id:
        return line_id
    try:
        return str(registry.get_active() or "")
    except Exception as e:  # noqa: BLE001  激活指针读不到按「未装线」处理
        logger.warning("[fan_out] 读取全局激活主线失败（按未装线处理）: %s", e)
        return ""


def _granted_tools_for_line(
    line_id: str,
    registry: Any,
    meta: Mapping[str, Any],
    available: List[str],
) -> Tuple[List[str], List[str], str]:
    """按主线装配该子代理的授权集（fail-closed）

    Args:
        line_id: 生效主线 id；空串 = 未装线 ⇒ 只读默认集。
        registry: ``LineRegistry``。
        meta: ``load_tool_meta()`` 的产物（plane/effect/risk 判定的唯一数据源）。
        available: 宿主真实注册的工具名（候选池）。

    Returns:
        ``(granted, needs_approval, note)``。

    Raises:
        _LineUnavailable: 主线不存在/已停用/档案损坏（该任务失败；**绝不**回退成全量授权）。
    """
    from agent.lines import assemble
    from agent.tools.subagent_tools import _default_subagent_tools

    if not line_id:
        base = [t for t in _default_subagent_tools() if t in meta]
        granted = [t for t in base if meta[t].plane != "govern"]
        return (granted, [t for t in granted if meta[t].needs_approval],
                "未指定 line 且无全局激活主线 ⇒ 使用只读默认集（写文件/Shell 不在内）")

    try:
        profile = registry.load(line_id)
    except Exception as e:  # noqa: BLE001  档案损坏同样按「不可用」处理，不回退
        raise _LineUnavailable(f"主线档案不可用: {line_id}（{e}）") from e
    if profile is None:
        raise _LineUnavailable(f"主线不存在: {line_id}（未回退成全量授权）")
    if not profile.enabled:
        raise _LineUnavailable(f"主线已停用: {line_id}（未回退成全量授权）")

    result = assemble(profile, available, meta=dict(meta))
    granted = list(result.tools)

    dropped_govern: List[str] = []
    if not profile.allow_govern:
        # 双保险：装配器在 allow_govern=False 时已剔除 govern 权重，此处再按
        # YAML 声明（唯一权威）过滤一次，防「权重表被改」把治理工具漏进来。
        dropped_govern = [t for t in granted if meta[t].plane == "govern"]
        granted = [t for t in granted if meta[t].plane != "govern"]

    needs = [t for t in granted if meta[t].needs_approval]
    if dropped_govern:
        note = (f"沿主线 {profile.id} 装配：已剔除 {len(dropped_govern)} 个 govern 平面工具"
                f"（{profile.id} 未开启 allow_govern）：{dropped_govern}")
    elif profile.allow_govern and needs:
        note = (f"沿主线 {profile.id} 装配：该线显式 allow_govern=true，"
                f"{len(needs)} 个工具需人工确认：{needs}")
    else:
        note = f"沿主线 {profile.id} 装配（{len(granted)} 个工具）"
    return granted, needs, note


def _tokens_used(outcome: Any) -> int:
    """该任务实际消耗的令牌（来自执行器成本记账；取不到按 0，不臆造）"""
    cost = getattr(outcome, "cost", None)
    total = getattr(cost, "total_tokens", 0)
    if isinstance(total, int) and not isinstance(total, bool):
        return max(0, total)
    return 0


def _task_result(
    index: int,
    line_id: str,
    *,
    status: str,
    summary: str = "",
    outcome: Any = None,
    error_code: str = "",
    error: str = "",
    tools_granted: Any = (),
    needs_approval: Any = (),
    toolset_note: str = "",
    executed: bool = False,
    budget_tokens: int = 0,
    timeout_seconds: float = 0.0,
    tokens_used: int = 0,
) -> Dict[str, Any]:
    """逐任务结果条目（字段名与任务书约定一致；额外字段只加叶子标量）"""
    item: Dict[str, Any] = {
        "index": int(index),
        "line": str(line_id or ""),
        "status": str(status),
        "summary": str(summary or ""),
        "outcome": outcome,
        "error_code": str(error_code or ""),
        "tools_granted": list(tools_granted),
        "needs_approval": list(needs_approval),
        "executed": bool(executed),
        "budget_tokens": int(budget_tokens),
        "timeout_seconds": float(timeout_seconds),
        "tokens_used": int(tokens_used),
    }
    if error:
        item["error"] = str(error)
    if toolset_note:
        item["toolset_note"] = str(toolset_note)
    return item


def _budget_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总预算与超时（**总额度 vs 实际消耗**，二者不可混为一谈）"""
    return {
        "allotted_tokens": sum(int(r.get("budget_tokens") or 0) for r in results),
        "allotted_timeout_seconds": round(
            sum(float(r.get("timeout_seconds") or 0.0) for r in results), 2),
        "executed_allotted_tokens": sum(
            int(r.get("budget_tokens") or 0) for r in results if r.get("executed")),
        "consumed_tokens": sum(int(r.get("tokens_used") or 0) for r in results),
    }


# ════════════════════════════════════════════════════════════
#  主流程（顺序即契约：先全校验 → 前置能力 → 逐任务装配 → 并发执行 → 汇总）
# ════════════════════════════════════════════════════════════


def _run_fan_out(dl: Any, kwargs: Mapping[str, Any]) -> Dict[str, Any]:
    """并行派发主流程"""
    # 惰性导入：工具注册发生在编排器初始化期，此处避免包级重依赖提前介入
    from agent.subagent.delegation import (
        E_DELEGATION_INCOMPLETE,
        ELEMENT_LABELS,
        DelegationContext,
    )
    from agent.subagent.toolset import SubAgentToolset
    from agent.tools.subagent_tools import (
        E_DELEGATION_NO_CHANNEL,
        _model_id,
        _result_from_outcome,
    )

    # ── 步骤 1：tasks 形态 ──
    raw_tasks = kwargs.get("tasks")
    if not isinstance(raw_tasks, (list, tuple)) or len(raw_tasks) == 0:
        return {
            "ok": False,
            "error_code": E_FAN_OUT_NO_TASKS,
            "error": ("tasks 必须是非空数组：每项是一个子代理的任务对象"
                      "（可选 line + 八要素齐全）"),
        }
    tasks: List[Any] = list(raw_tasks)
    limit = _max_tasks()
    if len(tasks) > limit:
        logger.warning("[fan_out] 任务数 %d 超过上限 %d，拒绝派发", len(tasks), limit)
        return {
            "ok": False,
            "error_code": E_FAN_OUT_TOO_MANY_TASKS,
            "error": (f"tasks 有 {len(tasks)} 项，超过单次上限 {limit}"
                      f"（成本放大防线，可用环境变量 {_ENV_MAX_TASKS} 调整）："
                      f"请拆成多批 fan_out 调用"),
        }

    # ── 步骤 2：**先全校验**（零副作用；任一任务不合格 ⇒ 整体拒绝）──
    elements_by_index = [_elements_of(task) for task in tasks]
    problems_by_index = [
        (idx, _task_problems(task, elements_by_index[idx]))
        for idx, task in enumerate(tasks)
    ]
    rejected = [(idx, problems) for idx, problems in problems_by_index if problems]
    if rejected:
        detail = "；".join(
            f"第 {idx + 1} 个任务：{_format_problems(problems)}"
            for idx, problems in rejected)
        logger.warning("[fan_out] 八要素不合格，整体拒绝（%d/%d 个任务）: %s",
                       len(rejected), len(tasks), [idx + 1 for idx, _ in rejected])
        return {
            "ok": False,
            "error_code": E_DELEGATION_INCOMPLETE,
            "error": (f"tasks 中有 {len(rejected)}/{len(tasks)} 个任务八要素不合格，"
                      f"已整体拒绝（未启动任何子代理）：{detail}"),
            "tasks_problems": [
                {
                    "index": idx,
                    "problems": dict(problems),
                    "missing_labels": [
                        ELEMENT_LABELS.get(name, name) for name in problems
                    ],
                }
                for idx, problems in rejected
            ],
        }

    # ── 步骤 3：前置能力（管理器 / 执行通道；都不跑注定失败的空执行）──
    manager = getattr(dl, "_subagent_mgr", None)
    if manager is None:
        logger.error("[fan_out] 子代理生命周期管理器不可用（dl._subagent_mgr 为空）")
        return {"ok": False, "error": (
            "子代理生命周期管理器不可用: dl._subagent_mgr 为空"
            "（subagent.enabled=False 或核心系统未初始化）")}
    if not callable(getattr(manager, "delegate", None)):
        logger.error("[fan_out] 生命周期管理器缺少 delegate(): %s", type(manager).__name__)
        return {"ok": False, "error": (
            f"子代理生命周期管理器不可用: {type(manager).__name__} 未提供 delegate()")}

    llm = getattr(dl, "_llm", None)
    from agent.subagent.channel import default_agent_cli

    if llm is None and not default_agent_cli():
        logger.error("[fan_out] 未配置执行通道：既无 LLM 也无外部 agent CLI")
        return {"ok": False, "error_code": E_DELEGATION_NO_CHANNEL, "error": (
            "未配置执行通道（既无 LLM 也无外部 agent CLI）: dl._llm 为空且环境变量 "
            "CP_SUBAGENT_AGENT_CLI 未设置；请先完成 LLM 配置或配置外部 agent CLI")}

    concurrency, concurrency_note = _clamp_concurrency(kwargs.get("max_concurrency"))

    # ── 步骤 4：逐任务按主线装配工具集（只读；失败的任务不执行、不回退）──
    from agent.lines import get_line_registry, load_tool_meta

    registry = get_line_registry()
    meta = load_tool_meta()
    available = _available_tool_names()

    parent_trace_id = ""
    try:  # 有编排 Trace 时串上父链；无则留空（不阻断派发）
        from agent.monitoring.tracing import get_trace_id

        parent_trace_id = str(get_trace_id() or "")
    except Exception:  # noqa: BLE001  Trace 不可用不影响派发
        parent_trace_id = ""

    short = uuid.uuid4().hex[:8]
    results: List[Any] = [None] * len(tasks)
    line_cache: Dict[str, Dict[str, Any]] = {}
    prepared: List[Dict[str, Any]] = []

    for idx, task in enumerate(tasks):
        elements = elements_by_index[idx]
        requested = str(task.get("line") or "").strip() if isinstance(task, Mapping) else ""
        line_id = _resolve_line(requested, registry)

        entry = line_cache.get(line_id)
        if entry is None:
            try:
                granted, needs, note = _granted_tools_for_line(
                    line_id, registry, meta, available)
                entry = {"ok": True, "granted": granted,
                         "needs_approval": needs, "note": note}
            except _LineUnavailable as e:
                entry = {"ok": False, "error": str(e)}
            line_cache[line_id] = entry

        # 显式标注 Dict[str, Any]：混合取值会被推成 dict[str, object]，
        # 后面把 base["budget_tokens"] 传给 typed 形参就会报 arg-type（mypy 实测踩过）
        base: Dict[str, Any] = {
            "index": idx,
            "line": line_id,
            "budget_tokens": int(elements["budget_tokens"]),
            "timeout_seconds": float(elements["timeout_seconds"]),
        }
        if not entry["ok"]:
            logger.warning("[fan_out] 第 %d 个任务主线不可用: %s", idx + 1, entry["error"])
            results[idx] = _task_result(
                idx, line_id, status="failed",
                summary=entry["error"], error_code=E_FAN_OUT_LINE_UNAVAILABLE,
                error=entry["error"],
                budget_tokens=base["budget_tokens"],
                timeout_seconds=base["timeout_seconds"])
            continue

        # 三重交集：申请 ∩ 授权 − §7.0 矩阵拒绝（两者都用**同一份**真实工具名）
        toolset = SubAgentToolset.build(
            entry["granted"], entry["granted"], actor=f"sub_agent:fan_out-{idx + 1}")
        visible = list(toolset.visible_tools())
        base["tools_granted"] = visible
        base["needs_approval"] = entry["needs_approval"]
        base["note"] = entry["note"]
        prepared.append(base)

    # ── 步骤 5：构造委派上下文 + 分身配置（预算/超时逐任务透传给 ctx）──
    from agent.subagent.container import SubagentConfig
    from agent.subagent.lifecycle import SUB_REASON_SUBAGENT_UNAVAILABLE

    specs: List[Tuple[Any, Any]] = []
    item_by_index: Dict[int, Dict[str, Any]] = {}
    ctx_by_index: Dict[int, Any] = {}
    for item in prepared:
        idx = item["index"]
        elements = elements_by_index[idx]
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
            delegation_id=f"fan-{short}-{idx + 1}",
            metadata={
                # 逐任务授权集：权威来源是下面的 authorized_for 工厂，这里同时写进
                # metadata（会随 task_file 物化给子代理，也是旧执行器的兜底路径）
                "authorized_capabilities": list(item["tools_granted"]),
                "line": item["line"],
                "fan_out_index": idx,
            },
        )
        specs.append((SubagentConfig(name=f"fan-out-{short}-{idx + 1}",
                                     model_id=_model_id(llm)), ctx))
        item_by_index[idx] = item
        ctx_by_index[idx] = ctx

    # 逐任务工具集工厂：``execute_many`` 的 ``tools`` / ``authorized_capabilities`` 是
    # **整批共用**的单值，逐任务只能靠 ``tools_for`` / ``authorized_for``（与既有的
    # ``credentials_for`` 同款工厂语义）。这里申请与授权同源：都是该任务那条线的装配集。
    authorized_by_delegation = {
        ctx_by_index[item["index"]].delegation_id: tuple(item["tools_granted"])
        for item in prepared
    }

    def _tools_for(ctx: Any) -> Tuple[str, ...]:
        return authorized_by_delegation.get(str(getattr(ctx, "delegation_id", "")), ())

    # ── 步骤 6：并发执行（分身纪律走既有生命周期管理器；并发原语走 execute_many）──
    if specs:
        executor = _build_executor(llm)
        logger.info("[fan_out] 并发派发 %d 个子代理（max_concurrency=%d）",
                    len(specs), concurrency)
        try:
            # manager.delegate_many：逐任务建分身（守 max_subagents 上限 + TTL 取契约⑦）
            # → executor.execute_many 并发执行 → 统一回收；结果与 specs 等长同序
            outcomes = list(manager.delegate_many(
                specs, executor=executor, llm=llm, max_concurrency=concurrency,
                tools_for=_tools_for, authorized_for=_tools_for))
        except Exception as e:  # noqa: BLE001  批量入口异常 → 逐任务失败，不外抛
            logger.error("[fan_out] 批量执行入口异常: %s", e, exc_info=True)
            outcomes = []
            for idx, item in item_by_index.items():
                results[idx] = _task_result(
                    idx, item["line"], status="failed",
                    error_code=E_FAN_OUT_BATCH_FAILED, error=f"批量执行异常: {e}",
                    summary=f"批量执行异常: {e}",
                    tools_granted=item["tools_granted"],
                    needs_approval=item["needs_approval"],
                    toolset_note=item["note"],
                    budget_tokens=item["budget_tokens"],
                    timeout_seconds=item["timeout_seconds"])

        for item, outcome in zip(prepared, outcomes):
            idx = item["index"]
            mapped = _result_from_outcome(outcome, ctx_by_index[idx])
            sub_reason = str(getattr(outcome, "sub_reason", "") or "")
            results[idx] = _task_result(
                idx, item["line"],
                status="ok" if bool(getattr(outcome, "ok", False)) else "failed",
                summary=str(mapped.get("result") or mapped.get("error") or ""),
                outcome=mapped,
                error_code=str(mapped.get("error_code") or ""),
                error=str(mapped.get("error") or ""),
                tools_granted=item["tools_granted"],
                needs_approval=item["needs_approval"],
                toolset_note=item["note"],
                # 建分身失败（容量上限等）没有真正进入执行器 ⇒ 如实标注 executed=False
                executed=sub_reason != SUB_REASON_SUBAGENT_UNAVAILABLE,
                budget_tokens=item["budget_tokens"],
                timeout_seconds=item["timeout_seconds"],
                tokens_used=_tokens_used(outcome))

    # ── 步骤 7：汇总（部分成功如实报告；单个失败不让整体失败）──
    final: List[Dict[str, Any]] = []
    for idx in range(len(tasks)):
        item = results[idx]
        if item is None:
            item = _task_result(
                idx, item_by_index.get(idx, {}).get("line", ""), status="failed",
                error_code=E_FAN_OUT_NO_RESULT,
                error="执行器未返回该任务的结果（批次被中断）",
                summary="执行器未返回该任务的结果（批次被中断）")
        final.append(item)

    succeeded = sum(1 for r in final if r["status"] == "ok")
    failed = len(final) - succeeded
    if failed == 0:
        status = "all_succeeded"
    elif succeeded == 0:
        status = "all_failed"
    else:
        status = "partial"

    # 【ok 的语义（刻意取「至少有一个成功」）】本工具一次派发 N 个**互相独立**的任务，
    # 单个子任务的失败不该让整次调用被当成"没干活"（那会让成功的产物被调用方丢弃）。
    # 因此 ok = succeeded > 0；部分失败由 status/partial/succeeded/failed + 逐任务
    # error 如实表达，全部失败时另给顶层 error_code/error（与 delegate 的失败口径一致）。
    govern_granted = sorted({
        t for item in prepared for t in item["tools_granted"]
        if t in meta and meta[t].plane == "govern"
    })
    failed_items = [r for r in final if r["status"] != "ok"]
    envelope: Dict[str, Any] = {
        "ok": succeeded > 0,
        "status": status,
        # 部分成功（既非全成也非全败）——单独给一个布尔位，避免调用方靠数数判断
        "partial": 0 < succeeded < len(final),
        "total": len(final),
        "succeeded": succeeded,
        "failed": failed,
        "max_concurrency": concurrency,
        "budget": _budget_summary(final),
        "results": final,
        "needs_approval": sorted({t for item in final for t in item["needs_approval"]}),
        "govern_tools_granted": govern_granted,
        "notes": [
            note for note in [
                concurrency_note,
                ("子代理按各自主线的装配集授权（results[*].tools_granted）；逐任务额度与"
                 "实际消耗见 results[*].tokens_used 与 budget"),
                ("分身由既有生命周期管理器批量创建/回收（受 subagent.max_subagents 约束，"
                 "TTL 取各任务契约⑦）"),
            ] if note
        ],
    }
    if failed:
        envelope["error_summary"] = (
            f"{failed}/{len(final)} 个子任务失败：" + "；".join(
                f"第 {r['index'] + 1} 个（{r['error_code'] or '未知错误'}："
                f"{(r.get('error') or r.get('summary') or '无原因')[:120]}）"
                for r in failed_items))
    if succeeded == 0 and failed_items:
        envelope["error_code"] = str(failed_items[0]["error_code"] or "")
        envelope["error"] = "全部子任务失败：" + str(
            failed_items[0].get("error") or failed_items[0].get("summary") or "")
    logger.info("[fan_out] 派发完成: total=%d succeeded=%d failed=%d status=%s",
                envelope["total"], succeeded, failed, status)
    return envelope


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════


def register_all(dl):
    """注册并行多线委派工具（``fan_out``）

    Args:
        dl: DigitalLife / LifecycleManager 实例（工具注册方传入的 self）；
            经 ``dl._subagent_mgr`` 取**既有**生命周期管理器（能力前置校验），
            经 ``dl._llm`` 取**既有** LLM。
    """

    @_tools.register("fan_out",
        "并行派发多条主线（fan_out / parallel delegate / multi-agent）：一次调用把 N 个任务"
        "**同时**发给 N 个子代理，每个子代理按 task.line 指定的主线档案（data/agent_lines/*.yaml）"
        "装配自己的工具集。tasks 中每一项都必须写全八要素——①目标 ②约束 ③已有成果 ④禁止事项 "
        "⑤产物格式 ⑥预算令牌 ⑦超时 ⑧回调地址：任一任务缺一项即**整体拒绝**（不会只跑通过的那部分）；"
        "③已有成果、④禁止事项确实没有时传空列表 []（「声明为空」与「未声明」是两件事），②约束不得为空列表。"
        "line 可选：缺省用全局激活主线，没有激活主线时只给只读默认集；主线不存在或已停用会让**该任务失败**"
        "（绝不回退成全量授权）。govern 平面工具（改云枢自身能力集的那些）默认不授予子代理，"
        "只有该主线显式 allow_govern: true 才放行并标注 needs_approval。"
        "单个子任务失败不会让整个 fan_out 失败：返回里 succeeded/failed 如实计数，逐任务结果在 results。"
        "Dispatch N tasks to N subagents concurrently, each with its own line-assembled toolset.",
        schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "并发派发的任务清单：每项对应一个子代理，每项都必须写全八要素（可选 line 指定主线）。任一项不合格即整体拒绝，一个子代理都不会启动。示例：[{\"line\": \"engineering\", \"goal\": \"把 docs/zh 下 12 篇设计稿抽取为可复现步骤序列\", \"constraints\": [\"只读仓库\"], \"prior_artifacts\": [], \"prohibitions\": [], \"artifact_format\": \"JSON Lines：每行 {name, steps[]}\", \"budget_tokens\": 20000, \"timeout_seconds\": 600, \"callback_url\": \"internal://fan_out\"}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line": {"type": "string", "description": "主线 id（已有 7 条：assistant / dev / digital_life / engineering / harness / knowledge / recon）。缺省用全局激活主线；无激活主线时只给只读默认集（读文件/检索类，写入与 Shell 不在内）。主线不存在或已停用 ⇒ 该任务失败，不会降级成全量授权"},
                            "goal": {"type": "string", "description": "①目标：子代理要达成的结果，必须具体可判定（至少 8 字符）。含糊目标会被拒绝——80% 的委派失败源于此"},
                            "constraints": {"type": "array", "items": {"type": "string"}, "description": "②约束：必须遵守的边界条件，字符串列表且**不得为空列表**（无约束的委派等同于未声明边界）。示例：[只读仓库，不得修改任何文件, 不得访问网络]"},
                            "prior_artifacts": {"type": "array", "items": {"type": "string"}, "description": "③已有成果：子代理可直接复用的既有产物引用（文件路径/URL/标识）。确实没有时传空列表 []，**不得省略该字段**"},
                            "prohibitions": {"type": "array", "items": {"type": "string"}, "description": "④禁止事项：明确不许做的事项。确实没有时传空列表 []，**不得省略该字段**。示例：[不得删除任何文件, 不得对外发送数据]"},
                            "artifact_format": {"type": "string", "description": "⑤产物格式：子代理必须交付的形态，需可机器判定。示例：JSON Lines：每行 {name, steps[]}"},
                            "budget_tokens": {"type": "integer", "description": "⑥预算令牌：本任务允许消耗的令牌上限，正整数且大于 0（逐任务透传给该子代理的委派契约，并计入汇总预算）"},
                            "timeout_seconds": {"type": "integer", "description": "⑦超时：本任务的最长执行秒数，正整数且大于 0（执行器据此设执行超时并据此时长回收分身）"},
                            "callback_url": {"type": "string", "description": "⑧回调地址：结果投递目标，非空字符串（如 internal://fan_out 或 https://…）。空串/纯空白等同于未声明，会被拒绝"},
                        },
                        "required": [
                            "goal", "constraints", "prior_artifacts", "prohibitions",
                            "artifact_format", "budget_tokens", "timeout_seconds",
                            "callback_url",
                        ],
                    },
                },
                "max_concurrency": {"type": "integer", "description": "并发上限（§4.2）：合法区间 1..DEFAULT_MAX_CONCURRENCY（当前 4），缺省 4；越界会被 clamp 到区间内并在返回的 notes 里说明"},
            },
            "required": ["tasks"],
        })
    def _fan_out(**kwargs):
        """并行派发（逐任务八要素校验 → 按主线装配 → execute_many；异常一律收口）"""
        try:
            return _run_fan_out(dl, kwargs)
        except Exception as e:  # noqa: BLE001  工具链路上绝不外抛异常
            logger.error("[fan_out] 并行委派执行异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"并行委派执行异常: {e}"}


__all__ = ["register_all"]
