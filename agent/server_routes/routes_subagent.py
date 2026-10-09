"""分身 (Subagent) API 路由

提供分身的创建、查询、执行、销毁等 REST API。

【两条执行路径的区别（2026-09-19 补）】
  - ``POST /api/subagent/<name>/execute``：走 ``SubagentContainer.execute()`` —— 设计上
    保持"占位骨架"（**不调 LLM、不做委派协议**，只回一段占位文案）。前端若接这条，
    表现就是"点了委托执行、返回 200 但什么都没干" ⇒ 即"子代理没跑通"。
  - ``POST /api/subagent/<name>/delegate``：走 ``SubagentContainer.run_delegation()``
    → ``DelegationExecutor``（八要素校验 → 工具裁剪 → 隔离执行 → Trace → 成本记账 →
    回收三件套），与模型侧 ``delegate`` 工具**同一条真执行链路**。UI 用这条。
"""

import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, Sequence

from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route

# 【P1-front 第三批 · 2026-10-05】统一响应信封（阶段 2 / R3）。
# 只依赖 stdlib + flask，不反向依赖 app_server。
from agent.api_envelope import ok as _ok
from agent.subagent.delegation_history import delegation_history
from agent.subagent.task_board import task_board
# S5 通信 · 静态对端：读面用的纯逻辑表（只读环境变量；不做任何服务发现/探测）
from agent.subagent.peers import read_snapshot

# 二次派发轮次机制（P4/P5 通信）：请求里的 previous_delegation_id 转成第二轮上下文。
# RoundError 是 fail-closed 判定结果（调用方转 400）——纯 stdlib 模块，导入零成本。
from agent.subagent.rounds import RoundError

# P3 brokered 档：档位词表/校验的唯一口径（仅 stdlib 依赖，不拉记忆重模块）
from agent.subagent.memory_broker import (
    MemoryConfigError,
    memory_view,
    resolve_memory_config,
)

# 密钥闸门异常（credentials.py 既有）：bundle 导出被拦下时要回明确错误码
from agent.subagent.credentials import ManifestSecretLeak

logger = logging.getLogger(__name__)

#: 入站回调请求体上限（64KiB）——超过即 413，不解析、不落账
CALLBACK_MAX_BODY_BYTES = 64 * 1024
#: 入站回调幂等的进程内锁（配合 delegation_history.find 只读判重，防并发重复落账）
_CALLBACK_DEDUP_LOCK = threading.Lock()


def _channel_info(Yunshu) -> dict:
    """委派执行通道可用性（真委派需要 LLM 或外部 agent CLI；缺一即拒绝执行）

    与 ``agent/tools/subagent_tools.py::_run_delegate`` 步骤 6 同一判据：没有执行通道时
    **明确拒绝**，不跑注定失败的空执行。UI 据此提前提示，避免用户点了"委托执行"
    却只拿到占位文案（那正是"子代理没跑通"的表象）。
    """
    llm = getattr(Yunshu, "_llm", None)
    cli = ""
    try:
        from agent.subagent.channel import default_agent_cli
        cli = str(default_agent_cli() or "")
    except Exception:  # noqa: BLE001 通道探测失败按"无 CLI"处理
        cli = ""
    local = False
    try:
        from agent.subagent.local_inference import local_backend_enabled
        local = bool(local_backend_enabled())
    except Exception:  # noqa: BLE001 本地档探测失败按"未开启"处理
        local = False
    return {
        "llm": llm is not None,
        "cli": bool(cli),
        "agent_cli": cli,
        # 本地推理档（显式开启才算可用；断网场景下的第三通道）——不加这一项，
        # 界面/工具侧仍会以 E_DELEGATION_NO_CHANNEL 拒绝，断网档等于没做
        "local": local,
        "ok": llm is not None or bool(cli) or local,
    }


def _active_line_id() -> str:
    """当前生效主线 id（bundle 装配单的 line 入参；读不到按"未点名"处理）

    与 agent/lines/integration.py::resolve_line_id 同源，不自己读激活指针文件。
    空串是真语义：导入侧据此走只读默认集（不是出错）。
    """
    try:
        from agent.lines import resolve_line_id

        return str(resolve_line_id() or "")
    except Exception as e:  # noqa: BLE001 主线子系统不可用按"未点名"处理
        logger.debug("[SubagentAPI] 生效主线读取失败（按未点名处理）: %s", e)
        return ""


def _bundle_assembly(line_id: str):
    """bundle 导出用的装配单快照（fail-soft：不可用则**如实**不带授权快照）

    唯一权威仍是 agent/subagent/assembly.py::resolve_subagent_assembly；本函数只
    提供它的两个入参（工具元数据 + 候选池），不重写装配算法。装配单不可用时少一段
    快照，而不是让整个导出失败——导出的是"这个分身"，不是"它的装配预览"。
    """
    try:
        from agent.lines import get_line_registry, load_tool_meta
        from agent.subagent.assembly import resolve_subagent_assembly

        meta = load_tool_meta()
        available = sorted(meta.keys())
        return resolve_subagent_assembly(str(line_id or ""), get_line_registry(),
                                         meta, available)
    except Exception as e:  # noqa: BLE001 装配单不可用不得让导出挂掉
        logger.warning("[SubagentAPI] 装配单投影不可用（bundle 导出照常，不带授权快照）: %s", e)
        return None


def _outcome_payload(outcome, ctx) -> dict:
    """``ExecutionOutcome`` → JSON（复用工具侧同一份字段映射，避免两套口径漂移）

    工具侧 ``subagent_tools._result_from_outcome`` 是委派结果对外形状的**单一来源**，
    这里优先复用；极端情况下（导入失败）退化为最小字段集，不让接口 500。
    """
    try:
        from agent.tools.subagent_tools import _result_from_outcome
        return _result_from_outcome(outcome, ctx)
    except Exception as e:  # noqa: BLE001 映射失败降级（不阻断响应）
        logger.debug("[SubagentAPI] 结果映射降级: %s", e)
        return {
            "ok": bool(getattr(outcome, "ok", False)),
            "delegation_id": str(getattr(outcome, "delegation_id", "") or ""),
            "tier": str(getattr(outcome, "tier", "") or ""),
            "duration_ms": round(float(getattr(outcome, "duration_ms", 0.0) or 0.0), 2),
            "trace_id": str(getattr(outcome, "trace_id", "") or ""),
            "result": str(getattr(outcome, "output_text", "") or ""),
            "error_code": str(getattr(outcome, "error_code", "") or ""),
            "error": str(getattr(outcome, "error", "") or ""),
        }


def _as_str_list(value, field: str, *, allow_empty: bool) -> list:
    """请求字段归一化为字符串列表（单串按 1 元素处理）"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} 必须是字符串列表")
    out = [str(v).strip() for v in value if str(v).strip()]
    if not out and not allow_empty:
        raise ValueError(f"{field} 不得为空列表（无约束的委派等同于未声明边界）")
    return out


def _clamp_limit(value, *, default: int, lo: int, hi: int) -> int:
    """limit 参数收敛（非法/越界一律回落到合法区间）"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

def _goal_from_body(data: dict) -> str:
    """请求体 → 目标（``task`` 与 ``goal`` 同义；别名是历史包袱，保持兼容）"""
    return str(data.get("task") or data.get("goal") or "").strip()


def _short_goal_response():
    """目标过短的统一拒绝（两个入口同一句文案，避免"改一处漏一处"）"""
    return jsonify({
        "ok": False, "error_code": "E_DELEGATION_INCOMPLETE",
        "error": "目标过短：至少 8 字符且需具体可判定（含糊目标会被拒绝）",
    }), 400


def _no_channel_response(channel: dict):
    """无执行通道的统一拒绝：**明确拒绝**，不跑注定失败的空执行"""
    return jsonify({
        "ok": False, "error_code": "E_DELEGATION_NO_CHANNEL",
        "error": ("未配置执行通道（既无 LLM 也无外部 agent CLI）："
                  "_llm 为空且环境变量 CP_SUBAGENT_AGENT_CLI 未设置"),
        "channel": channel,
    }), 409


def _llm_view(Yunshu: Any, declared: Any = ()) -> Dict[str, Any]:
    """部署级 LLM 事实 + 可选模型清单（**逐分身**生效来源见 `_subagent_with_llm`）

    【为什么在列表端点里回这个】装配车间要回答两个问题：①"这个分身实际会用哪个
    模型"②"我能填哪些模型名"。两者都只有后端知道（母体当前模型 + 各分身声明），
    前端自己猜就是第二份口径。**不新增路由**：与 `channel` 同款，作为本页的自举载荷。
    【不编造模型目录】候选只列"有出处"的名字（部署默认 + 各分身已声明），见
    `agent/subagent/llm_factory.py::llm_options`。
    """
    try:
        from agent.subagent.llm_factory import llm_options, model_name

        parent = getattr(Yunshu, "_llm", None)
        view = llm_options(model_name(parent), declared)
        view["provider"] = str(getattr(parent, "provider", "") or "")
        return view
    except Exception as e:  # noqa: BLE001 提示性载荷不可用不得让列表挂掉
        logger.warning("[SubagentAPI] LLM 视图不可用（列表照常返回）: %s", e)
        return {"model": "", "provider": "", "options": []}


def _subagent_with_llm(subagent: Any, parent_llm: Any) -> Dict[str, Any]:
    """给一条分身状态补上"它实际会用的模型"（来源三档，见 llm_factory 模块 docstring）

    单个分身解析失败只影响该行（补一条 `fallback-after-error`），列表整体照常返回。
    """
    row: Dict[str, Any] = dict(subagent or {})
    try:
        from agent.subagent.llm_factory import resolve_subagent_llm

        row["llm"] = resolve_subagent_llm(row.get("model_id", ""),
                                          parent_llm=parent_llm,
                                          temperature=row.get("llm_temperature")).to_dict()
    except Exception as e:  # noqa: BLE001
        logger.warning("[SubagentAPI] 分身 %s 的 LLM 解析失败: %s", row.get("name"), e)
        row["llm"] = {"requested": str(row.get("model_id") or ""), "model": "",
                      "source": "fallback-after-error", "error": str(e)}
    return row


def _role_view() -> Dict[str, Any]:
    """部署级角色事实（受控模板词表 + 三档语义），随列表载荷一并返回（**不新增路由**）

    与 `_llm_view` 同款：前端不自己维护第二份模板清单（那会与后端分叉）。
    """
    try:
        from agent.subagent.role_templates import role_catalog

        return role_catalog()
    except Exception as e:  # noqa: BLE001 提示性载荷不可用不得让列表挂掉
        logger.warning("[SubagentAPI] 角色视图不可用（列表照常返回）: %s", e)
        return {"templates": [], "tiers": [], "default_tier": "template",
                "role_text_max_chars": 0}


def _subagent_with_role(row: Dict[str, Any]) -> Dict[str, Any]:
    """给一条分身状态补上"它的角色生效情况"（模板 / 档位 / 是否红档 / 是否需审计）

    `container.get_status()` 已带 `role` 段；本函数只兜底那些**没有容器的行**
    （例如测试替身或未来其它数据源），口径仍是同一个 `resolve_subagent_role`。
    投影**不含 role_text 正文**（列表载荷不该携带自由文本）。
    """
    out = dict(row or {})
    if "role" in out:
        return out
    try:
        from agent.subagent.role_templates import resolve_subagent_role

        out["role"] = resolve_subagent_role(
            out.get("role_template", ""), out.get("role_text", ""),
            out.get("role_mode", "template")).to_dict()
    except Exception as e:  # noqa: BLE001
        logger.warning("[SubagentAPI] 分身 %s 的角色解析失败: %s", out.get("name"), e)
        out["role"] = {"template": "", "tier": "", "source": "", "fragment_chars": 0,
                       "constraints": 0, "audit_required": False, "red": False,
                       "error": str(e)}
    return out


def _live_role_config(Yunshu: Any, name: str) -> Dict[str, Any]:
    """取**存活容器**上的角色字段（热更新未传字段时沿用现值）

    【为什么不能读状态投影】状态里**刻意不含 `role_text` 正文**（列表载荷不该携带
    自由文本）；热更新若从状态取值，就会把已有 `role_text` 静默清空 —— 那就成了
    "改个模型顺手把角色说明抹了"。
    """
    mgr = getattr(Yunshu, "_subagent_mgr", None)
    container = mgr.get(name) if callable(getattr(mgr, "get", None)) else None
    cfg = getattr(container, "config", None)
    if cfg is None:
        return {}
    return {"role_template": getattr(cfg, "role_template", ""),
            "role_text": getattr(cfg, "role_text", ""),
            "role_mode": getattr(cfg, "role_mode", "template")}


def _role_fields(data: Dict[str, Any], base: Dict[str, Any] | None = None) -> Dict[str, str]:
    """读并**校验**角色的三个字段（创建 / 热更新共用一份口径）

    校验在**进门时**做（而不是等到委派）：未知模板 / 默认档配自由文本都必须立刻 400，
    理由与 `_llm_temperature` 越界同款 —— 不做静默夹取、不把错误留到运行时。

    Raises:
        ValueError: 角色配置无效（调用方转 400）。
    """
    from agent.subagent.role_templates import resolve_subagent_role

    base = base or {}
    template = str(data.get("role_template", base.get("role_template", "")) or "").strip()
    text = str(data.get("role_text", base.get("role_text", "")) or "")
    mode = data.get("role_mode", base.get("role_mode", "template"))
    plan = resolve_subagent_role(template, text, mode)
    if plan.error:
        raise ValueError(f"角色配置无效：{plan.error}")
    return {"role_template": template, "role_text": text, "role_mode": plan.tier}


def _live_memory_config(Yunshu: Any, name: str) -> Dict[str, Any]:
    """取**存活容器**上的记忆档位字段（热更新未传字段时沿用现值）

    与 `_live_role_config` 同款：memory_scope 是**标识**不是正文，但热更新仍读存活
    容器的配置（不从状态投影取值），避免"改个模型顺手把记忆域清了"。
    """
    mgr = getattr(Yunshu, "_subagent_mgr", None)
    container = mgr.get(name) if callable(getattr(mgr, "get", None)) else None
    cfg = getattr(container, "config", None)
    if cfg is None:
        return {}
    return {"memory_mode": getattr(cfg, "memory_mode", "none"),
            "memory_scope": getattr(cfg, "memory_scope", None),
            "memory_quota": getattr(cfg, "memory_quota", None)}


def _memory_fields(data: Dict[str, Any], base: Dict[str, Any] | None = None,
                   provider: Any = None) -> Dict[str, Any]:
    """读并**校验**记忆字段（创建 / 热更新 / 临时委派共用一份口径）

    校验在**进门时**做（而不是等到委派）：未知档位 / scoped 缺三要素或 provider /
    默认档配非空域或配额 / 未知配额键都必须立刻 400，与 `_role_fields` 同款 ——
    不静默夹取、不把错误留到运行时。

    Args:
        data: 请求体。
        base: 未传字段时的现值（热更新用）。
        provider: 生效 memory_provider（scoped 要求非空；由调用方从 config 取值）。

    Raises:
        MemoryConfigError: 记忆配置非法（调用方转 400 E_MEMORY_CONFIG）。
    """
    base = base or {}
    mode = data.get("memory_mode", base.get("memory_mode", "none"))
    scope = data.get("memory_scope", base.get("memory_scope"))
    quota = data.get("memory_quota", base.get("memory_quota"))
    if provider is None:
        provider = data.get("memory_provider", base.get("memory_provider", ""))
    plan = resolve_memory_config(mode, scope, provider, quota)
    if plan.error:
        raise MemoryConfigError(plan.error)
    return {"memory_mode": plan.mode,
            "memory_scope": (dict(plan.scope) or None),
            "memory_quota": (dict(plan.quota) or None)}


def _subagent_with_memory(row: Dict[str, Any]) -> Dict[str, Any]:
    """给一条分身状态补上 memory 段（兜底没有容器的行；口径仍是同一个 resolve）"""
    out = dict(row or {})
    if "memory" in out:
        return out
    plan = resolve_memory_config(out.get("memory_mode", "none"), out.get("memory_scope"),
                                 out.get("memory_provider", ""), out.get("memory_quota"))
    view = plan.to_dict()
    view["provider"] = str(out.get("memory_provider", "") or "")
    out["memory"] = view
    return out


def _llm_temperature(data: Dict[str, Any], default: Any = None) -> Any:
    """读并校验分身的生成温度（``llm_temperature``）

    ``None`` / 缺省 / 空串 ⇒ 返回 ``None``（**不干预**执行器默认，不是 0.0：
    "没表态"与"要最确定性"是两回事）。给了值就**必须**是 0.0–2.0 的有限数
    （越界即 ValueError，调用方转 400）—— 不做静默夹取：悄悄把 3.0 改成 2.0
    就是替使用者改了参数，而他会以为 3.0 生效了。
    """
    raw = data.get("llm_temperature", default)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"llm_temperature 必须是数字（0.0–2.0），收到: {raw!r}") from e
    if value != value or value in (float("inf"), float("-inf")):  # NaN / ±inf
        raise ValueError(f"llm_temperature 必须是有限数字，收到: {raw!r}")
    if value < 0.0 or value > 2.0:
        raise ValueError(f"llm_temperature 越界（须在 0.0–2.0）：{value}")
    return value


def _granted_tools() -> tuple:
    """子代理工具子集（与模型侧 ``delegate`` 工具**同源**：同一份只读白名单）

    两处各写一份 ⇒ 两套授权口径，本仓反复出现的老形态。白名单不可用时退化为空集
    （纯推理委派），不阻断委派本身。
    """
    try:
        from agent.tools.subagent_tools import _default_subagent_tools
        return tuple(_default_subagent_tools())
    except Exception as e:  # noqa: BLE001 白名单不可用 → 纯推理委派（不授予工具）
        logger.warning("[SubagentAPI] 工具白名单不可用，按纯推理委派执行: %s", e)
        return ()


def _build_context(data: dict, task: str, *, delegation_prefix: str = "dlg-ui",
                   extra_constraints: Sequence[str] = ()):
    """请求体 → ``DelegationContext``（八要素缺省补齐）

    **两个入口共用**（具名 ``/api/subagent/<name>/delegate`` 与临时分身
    ``/api/subagent/delegate``）：缺省规则各写一份必然漂移，而"缺省补齐"正是
    UI 简化入口能被接受的全部理由。

    Args:
        extra_constraints: 追加进 **②约束** 的行（当前唯一来源 = 角色模板
            ``template+text`` 档的自由文本，见 ``agent/subagent/role_templates.py``）。
            追加在调用方约束**之后**：调用方的边界在前，角色补充在后，读起来是
            "先满足这些边界，再按这个角色做"。

    Raises:
        ValueError: ⑥预算 / ⑦超时不是整数（调用方转 400）。
    """
    from agent.subagent.delegation import DelegationContext

    constraints = _as_str_list(data.get("constraints"), "constraints", allow_empty=False) \
        or ["只读为主，不得修改仓库文件"]
    # 角色自由文本（template+text 档）追加在调用方约束之后；空元组 ⇒ 逐字不变
    constraints = list(constraints) + [str(c) for c in extra_constraints if str(c).strip()]
    prohibitions = _as_str_list(data.get("prohibitions"), "prohibitions", allow_empty=True)
    if not prohibitions:
        prohibitions = ["不得对外发送数据"]
    prior_artifacts = _as_str_list(data.get("prior_artifacts"), "prior_artifacts", allow_empty=True)
    try:
        budget_tokens = int(data.get("budget_tokens") or 4000)
        timeout_seconds = int(data.get("timeout_seconds") or 120)
    except (TypeError, ValueError) as e:
        raise ValueError("budget_tokens / timeout_seconds 必须是整数") from e

    return DelegationContext(
        goal=task,
        constraints=constraints,
        prior_artifacts=prior_artifacts,
        prohibitions=prohibitions,
        artifact_format=str(data.get("artifact_format") or "文本要点"),
        budget_tokens=budget_tokens,
        timeout_seconds=timeout_seconds,
        # ⑧回调地址：八要素校验只要求"已声明"（空串 ⇒ E_DELEGATION_INCOMPLETE）。
        # UI 是同步调用、结果直接由本响应返回，故用本地占位标识：投递器缺省只记审计、
        # 不会真的外呼；需要真投递时调用方显式传 callback_url。
        callback_url=str(data.get("callback_url") or "ui://workbench/sync"),
        delegation_id=f"{delegation_prefix}-{uuid.uuid4().hex[:8]}",
    )


def _apply_follow_up(data: dict, ctx: Any):
    """把请求里的 previous_delegation_id 解析成第二轮上下文（**fail-closed**）

    【为什么不静默忽略】跟单基准必须是**真实上过板**的那一轮；查不到/跨域/已是
    第二轮就拒绝（调用方转 400），绝不悄悄退化成"普通第一轮" —— 否则用户以为在
    做校验、实际另起炉灶，而看板上看不出差别（见 agent/subagent/rounds.py 边界）。

    Args:
        data: 请求体（读 previous_delegation_id）。
        ctx: 已补齐八要素的委派上下文。

    Returns:
        (ctx, round_info)。首轮时 round_info 仍返回（round=1、follow_up=False），
        与 llm / role / memory 等回显段同款：**恒有**，调用方不必判空。

    Raises:
        RoundError: 上一轮不存在 / 跨租户或跨主体 / 已到最大轮次。
    """
    from agent.subagent.rounds import link_follow_up, round_view, validate_previous

    previous_id = str(data.get("previous_delegation_id") or "").strip()
    if not previous_id:
        return ctx, round_view(ctx)
    previous = task_board.find_delegation(previous_id)
    validate_previous(previous, tenant_id=getattr(ctx, "tenant_id", "default"),
                      subject_id=getattr(ctx, "subject_id", ""),
                      previous_delegation_id=previous_id)
    linked = link_follow_up(ctx, previous)
    return linked, round_view(linked)


def _elements_view(ctx) -> dict:
    """八要素回显：让调用方看到"实际是按什么跑的"（含缺省补齐后的值）"""
    return {
        "goal": ctx.goal,
        "constraints": list(ctx.constraints),
        "prior_artifacts": list(ctx.prior_artifacts),
        "prohibitions": list(ctx.prohibitions),
        "artifact_format": ctx.artifact_format,
        "budget_tokens": ctx.budget_tokens,
        "timeout_seconds": ctx.timeout_seconds,
        "callback_url": ctx.callback_url,
    }


def register_routes(app, state):
    """注册所有分身管理路由"""

    Yunshu = state.Yunshu

    # ═══════════════════════════════════════════════════
    #  列表 & 状态
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/list")
    @trace_route("Subagent")
    @log_request(show_response=False)
    def api_subagent_list():
        """获取所有活跃分身列表（附带委派执行通道可用性，供 UI 提前提示）。

        【P1-front 第三批】载荷装进 `ok()` 的 data 并带 `X-Envelope: v2`。
        消费方只有 `pages/hub/workshop/agents.tsx`。
        【2026-10-08 新增两段，仍**不新增路由**】：`llm`（部署级模型 + 可选清单，
        P1 加）与 `role`（受控角色模板词表 + 三档语义）；逐分身的生效情况分别在
        每行的 `llm` / `role` 里。`count`/`channel` 一并保留（UI 提前提示通道可用性）。
        """
        try:
            raw_subagents = Yunshu.list_subagents()
            parent_llm = getattr(Yunshu, "_llm", None)
            subagents = [_subagent_with_memory(_subagent_with_role(
                            _subagent_with_llm(sa, parent_llm)))
                         for sa in raw_subagents]
            return _ok({
                "ok": True,
                "subagents": subagents,
                "count": len(subagents),
                # UI 用：通道不可用时"委托执行"必然失败，提前提示而不是让用户白点
                "channel": _channel_info(Yunshu),
                # UI 用：部署级模型 + 可选模型清单（不新增路由；逐分身生效来源在各行 llm 里）
                "llm": _llm_view(Yunshu, [sa.get("model_id") for sa in subagents]),
                # UI 用：受控角色模板词表 + 三档语义（不新增路由；逐分身生效情况在各行 role 里）
                "role": _role_view(),
            })
        except Exception as e:
            logger.error("[SubagentAPI] 列表查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/capabilities")
    @trace_route("Subagent")
    @log_request(show_response=False)
    def api_subagent_capabilities():
        """主权分身能力投影（六层二十面 + 三态 + 证据）—— 组装台「主权清单」的数据源

        【为什么是一个只读投影】页面要显示"这一面到底拥不拥有主权"，而这件事**只有后端知道**
        （字段有没有消费者、机制在不在）。让前端自己判 = 第二份口径，迟早把"字段在、没人读"
        化妆成"已接线"——那正是组装台要消灭的误导。
        【为什么带 evidence_files】反幻觉：说某面"拥有"就必须能指到实现文件；
        守卫 `tests/unit/test_subagent_capabilities.py` 逐个断言这些路径真实存在。
        【不新增敏感面】只回能力状态与证据路径，不含任何配置值 / 密钥 / 运行时数据。
        【只读、无副作用】`agent/subagent/capabilities.py` 是唯一权威。
        """
        try:
            from agent.subagent.capabilities import sovereignty_report

            report = sovereignty_report()
            return jsonify({"ok": True, "capabilities": report})
        except Exception as e:  # noqa: BLE001 投影失败不得让整页挂掉
            logger.error("[SubagentAPI] 能力投影失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/history")
    @trace_route("Subagent")
    @log_request(show_response=False)
    def api_subagent_history():
        """委派记录（最近 N 条，**最新在前**）——「子代理」下拉的历史数据源

        【为什么需要它】存活分身列表（``/api/subagent/list``）只列**当前活着**的容器，
        而委派一律"跑完即回收"（``destroy_after=True``）⇒ 正常业务里那个列表几乎恒为空，
        用户看到的是"业务明明发生了、面板却什么都没有"。本端点回答"发生过什么"。

        数据源：每次真委派后由 ``agent/subagent/delegation_history.py`` 落的轻量 JSONL。
        两处咽喉各记一处、互不重叠：单发走 ``SubagentContainer.run_delegation``，
        批量走 ``SubagentLifecycleManager.delegate_many``（批量不经过容器）。
        记录里**不含交付物正文**（外来文本 + 体量不可控），只有可展示的元信息。

        【P4 共享任务看板】响应额外内联 board 段（**不新增路由**，先例：
        /api/subagent/list 内联 llm / role 段）：records 为按 task_id 折叠后的
        最新态、count 为折叠后条数、total 为看板原始事件数、write_failed 为
        写失败累计（fail-soft 的显式证据）。

        【S5 通信 · 静态对端】响应再内联 peers 段（同样**不新增路由**）：
        peers = ``CP_SUBAGENT_PEERS`` 静态声明的对端（name/url/enabled），
        health = 各对端健康折叠态，offline_fallback = 离线回退的显式登记
        （status=not_implemented）。**不做任何服务发现/探测**——这一段只回声明，
        不回「发现了谁」。心跳周期由 ``register_peer_heartbeats`` 提供，
        并在 app_server 启动期接线；读面取共享的 live registry，故 health
        里能看到心跳累计结果（未接线时回落为"每请求按环境现造"）。

        Query:
            limit (int, optional): 返回条数，缺省 20，收敛到 [1, 100]。

        【路由优先级】本规则是**静态**路径，Werkzeug 静态优先于 ``/api/subagent/<name>``，
        故不会被同名分身遮蔽（回归用例见 tests/unit/test_subagent_history_route.py）。
        """
        try:
            limit = _clamp_limit(request.args.get("limit"), default=20, lo=1, hi=100)
            records = delegation_history.query(limit=limit)
            return jsonify({
                "ok": True,
                "records": records,
                "count": len(records),
                # 总数：None = 文件过大未统计（"未统计" ≠ 0，UI 据此区分"没有记录"）
                "total": delegation_history.total(),
                # P4 共享任务看板：不新增路由，在既有响应里内联 board 段
                # （先例：/api/subagent/list 内联 llm / role 段）。
                "board": task_board.snapshot(limit=limit),
                # S5 通信 · 静态对端心跳：同样**不新增路由**，在既有响应里内联 peers 段。
                # 内容 = 静态声明的对端（url/enabled）+ 健康态 + 离线回退显式登记。
                # read_snapshot() 优先用**启动期共享的 live registry**（心跳写进去的健康态
                # 才看得见）；未接线时回落到"每请求读环境变量"的老口径（无探测、无缓存）。
                "peers": read_snapshot(),
                "ts": time.strftime("%H:%M:%S"),
            })
        except Exception as e:
            logger.error("[SubagentAPI] 委派记录查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e), "records": []}), 500

    @app.route("/api/subagent/<name>")
    @trace_route("Subagent")
    @log_request(show_response=False)
    def api_subagent_get(name):
        """获取指定分身详情"""
        try:
            subagent = Yunshu.get_subagent(name)
            if subagent is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404
            return jsonify({"ok": True, "subagent": subagent})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  可带走 bundle（S5）：导出 / 导入
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/<name>/bundle")
    @trace_route("Subagent")
    @require_token
    @log_request(show_response=False)
    def api_subagent_bundle(name):
        """导出可带走 bundle（只读；**导出前必经密钥闸**）

        【为什么只回引用不回值】secrets.refs 只有 source/name/env_var 三个键；
        值由到达端自己的 TTL 凭据管理器签发。整包过 credentials.py 的
        assert_manifest_secret_free —— 检出长期密钥时**拒绝导出**（fail-closed），
        而不是"照常导出、只记一条日志"。

        契约见 agent/subagent/bundle.py（schema_version / identity / assembly /
        secrets / entrypoint / runtime）。
        """
        try:
            mgr = getattr(Yunshu, "_subagent_mgr", None)
            container = mgr.get(name) if callable(getattr(mgr, "get", None)) else None
            if container is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404
            cfg = getattr(container, "config", None)
            if cfg is None:
                return jsonify({"ok": False,
                                "error": f"分身容器无配置，无法导出: {name}"}), 500

            from agent.subagent.bundle import build_bundle, secret_refs_from_env
            from agent.subagent.dependencies import build_dependency_manifest
            from agent.subagent.llm_factory import resolve_subagent_llm

            line_id = _active_line_id()
            resolution = resolve_subagent_llm(
                getattr(cfg, "model_id", ""),
                parent_llm=getattr(Yunshu, "_llm", None),
                temperature=getattr(cfg, "llm_temperature", None))
            bundle = build_bundle(
                cfg, line_id=line_id, llm_resolution=resolution,
                assembly=_bundle_assembly(line_id),
                secret_refs=secret_refs_from_env(),
                # 离线依赖清单（v2 environment 段）：只读 pyproject + 本机实装，不联网
                environment=build_dependency_manifest())
            return jsonify({"ok": True, "bundle": bundle})
        except ManifestSecretLeak as e:
            logger.error("[SubagentAPI] bundle 导出被密钥闸拦下 name=%s: %s", name, e)
            return jsonify({"ok": False, "error_code": "E_MANIFEST_SECRET_LEAK",
                            "error": str(e)}), 409
        except Exception as e:  # noqa: BLE001 导出失败要有明确错误码，不裸 500
            logger.exception("[SubagentAPI] bundle 导出失败: %s", e)
            return jsonify({"ok": False, "error_code": "E_BUNDLE_EXPORT_FAILED",
                            "error": str(e)}), 500

    @app.route("/api/subagent/import", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_import():
        """从 bundle 导入一个分身（非法 bundle ⇒ 400 且**不建容器**）

        【顺序纪律】先 validate / import_config（纯函数，不改任何状态），全部通过
        才调 create_subagent。任何一步失败都不会留下半成品容器。
        【name 冲突】由生命周期管理器判定并如实回（消歧不在本层另造一套）。

        返回 {ok, subagent, imported:{bundle_id, backend}}。
        """
        from agent.subagent.bundle import (
            BundleValidationError,
            UnsupportedBackend,
            bundle_from_json,
            get_backend,
            import_config,
            validate_bundle,
        )
        try:
            data = request.get_json(silent=True) or {}
            raw = data.get("bundle")
            if raw is None:
                return jsonify({"ok": False, "error_code": "E_BUNDLE_INVALID",
                                "error": "请求体缺少 bundle 字段"}), 400
            if isinstance(raw, str):
                # 允许直接投递 bundle_to_json 的文本；解析失败即 400
                raw = bundle_from_json(raw)
            problems = validate_bundle(raw)
            if problems:
                return jsonify({
                    "ok": False, "error_code": "E_BUNDLE_INVALID",
                    "error": "非法 bundle（未建容器）：" + "；".join(problems[:8]),
                    "problems": problems,
                }), 400
            backend = get_backend(raw)
            config = import_config(raw)
            container = Yunshu.create_subagent(config)
            # 到达端依赖对拍：导入成功 ≠ 可离线跑。offline_ready 是**包自己的声明**
            # （裸 JSON 导出恒 false，自带 wheelhouse 的离线包可为 true）；satisfied 是到达端实测。
            from agent.subagent.dependencies import check_against_bundle

            environment = raw.get("environment")
            return jsonify({
                "ok": True,
                "subagent": container.get_status(),
                "imported": {"bundle_id": str(raw.get("bundle_id") or ""),
                             "backend": backend,
                             "environment": environment},
                "environment_check": check_against_bundle(environment),
            })
        except BundleValidationError as e:
            return jsonify({"ok": False, "error_code": "E_BUNDLE_INVALID",
                            "error": str(e)}), 400
        except UnsupportedBackend as e:
            return jsonify({"ok": False, "error_code": "E_BUNDLE_BACKEND_UNSUPPORTED",
                            "error": str(e)}), 400
        except Exception as e:  # noqa: BLE001 与 create 同款：非法输入一律 400
            logger.error("[SubagentAPI] bundle 导入失败: %s", e)
            return jsonify({"ok": False, "error_code": "E_BUNDLE_IMPORT_FAILED",
                            "error": str(e)}), 400

    # ═══════════════════════════════════════════════════
    #  S5 通信：回调反向通道（入站）
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/callback", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request(show_body=False)
    def api_subagent_callback():
        """接收分身/远端的委派结果回调（**只落账，不执行任何工具、不建/改容器**）

        请求体：{delegation_id, status, trace_id?, summary?, artifact_count?, source?}
          · summary 只作**短摘要**（截断 ≤1000），**不执行**、不解析为工具调用；
          · 幂等：同一 delegation_id 重复投递 ⇒ 200 + deduplicated=true，不重复落账；
          · 缺/错 token ⇒ 401（@require_token）；体过大 >64KiB ⇒ 413；
            缺 delegation_id ⇒ 400；非法 JSON ⇒ 400。

        落账复用既有 delegation_history.append（与 record_outcome 同一份 JSONL 契约），
        只用只读 find 判重；响应**不回显**任何令牌或摘要正文。
        """
        # ① 体量闸门：先看声明的 Content-Length，读取后再复核（兼顾 chunked 无长度）
        declared = request.content_length
        if declared is not None and declared > CALLBACK_MAX_BODY_BYTES:
            return jsonify({"ok": False, "error_code": "E_CALLBACK_BODY_TOO_LARGE",
                            "error": f"回调请求体超过 {CALLBACK_MAX_BODY_BYTES} 字节"}), 413
        raw = request.get_data(cache=True) or b""
        if len(raw) > CALLBACK_MAX_BODY_BYTES:
            return jsonify({"ok": False, "error_code": "E_CALLBACK_BODY_TOO_LARGE",
                            "error": f"回调请求体超过 {CALLBACK_MAX_BODY_BYTES} 字节"}), 413
        # ② 解析（非法 JSON / 非对象一律 400，不静默当空对象）
        data: dict = {}
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                return jsonify({"ok": False, "error_code": "E_CALLBACK_BAD_JSON",
                                "error": f"回调请求体不是合法 JSON：{type(e).__name__}"}), 400
            if not isinstance(parsed, dict):
                return jsonify({"ok": False, "error_code": "E_CALLBACK_BAD_BODY",
                                "error": "回调请求体必须是 JSON 对象"}), 400
            data = parsed
        delegation_id = str(data.get("delegation_id") or "").strip()
        if not delegation_id:
            return jsonify({"ok": False, "error_code": "E_CALLBACK_NO_DELEGATION_ID",
                            "error": "请求体缺少 delegation_id"}), 400
        # ③ 幂等落账：进程锁内判重 + 追加（跨进程竞态如实登记为未做）
        from agent.subagent.callback_channel import build_callback_record

        with _CALLBACK_DEDUP_LOCK:
            if delegation_history.find(delegation_id, source="callback") is not None:
                return jsonify({"ok": True, "deduplicated": True,
                                "delegation_id": delegation_id}), 200
            written = delegation_history.append(
                build_callback_record(data, delegation_id))
        return jsonify({"ok": True, "deduplicated": False,
                        "delegation_id": delegation_id,
                        "recorded": bool(written)}), 200

    # ═══════════════════════════════════════════════════
    #  创建 & 销毁
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/create", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_create():
        """创建新分身

        POST JSON:
            name (str): 分身名称（唯一）
            model_id (str, optional): LLM 模型名；**空/缺省 = 跟随母体**（见下）
            memory_provider (str): 记忆提供商
            tool_sources (list[str], optional): 工具源列表
            permissions (list[str], optional): 权限列表（默认 ['read']）
            context_window (int, optional): 上下文窗口大小（默认 4096）
            tags (list[str], optional): 标签
            ttl_seconds (int, optional): 存活时间（0=永久）
            role_template (str, optional): 受控角色模板 id（`agent/subagent/role_templates.py`
                词表的键）；**空 = 未装角色**（system prompt 与改动前逐字相同）
            role_mode (str, optional): 角色档位。`template`（默认）/ `template+text`
                （自由文本只进 ②约束）/ `full-system`（自由文本进 system prompt：红档，
                **必须显式开启**，执行器写审计 + UI 红档徽章）
            role_text (str, optional): 角色自由文本。默认档下提供非空值 ⇒ 400
                （不静默忽略 —— 那会让使用者以为它生效了）；仅在显式开启的档位生效
            memory_mode (str, optional): 记忆档位。`none`（默认）/ `brokered`
                （母体按 memory_scope 取一段**只读、限定域**记忆进 ②约束；分身侧仍无
                任何记忆工具）/ `scoped`（分身自带私人记忆域，**显式开启的受控档**：
                必须三要素齐全且 memory_provider 非空，缺任一 ⇒ 400）
            memory_scope (dict, optional): 记忆域（tenant_id / workspace_id /
                subject_id / workspace_root / memory_types / limit）；brokered 使用；
                scoped 要求 tenant_id / workspace_id / subject_id 三要素齐全；
                默认档给非空值 ⇒ 400
            memory_quota (dict, optional): scoped 档配额（max_entries / max_bytes /
                consecutive_reject_limit）；非 scoped 给值 ⇒ 400（不静默忽略）

        【`model_id` 从"必填"改为"空 = 跟随母体"】此前必填，于是"跟随母体"只能靠**抄一个
        母体模型名**来伪装；部署一换模型，那串名字就从"跟随"变成"显式指定旧模型"——
        一个会过期的假选择。现在空串是**真语义**：解析时走
        `agent/subagent/llm_factory.py` 的 `inherit` 档（始终跟随母体当前模型），
        生效模型随 `/api/subagent/list` 与委派响应如实回显。
        """
        try:
            data = request.get_json() or {}

            required = ["name", "memory_provider"]
            missing = [k for k in required if k not in data]
            if missing:
                return jsonify({"ok": False, "error": f"缺少必要字段: {missing}"}), 400

            config = {
                "name": data["name"],
                "model_id": str(data.get("model_id") or "").strip(),
                "memory_provider": data["memory_provider"],
                "tool_sources": data.get("tool_sources", []),
                "permissions": data.get("permissions", ["read"]),
                "context_window": data.get("context_window", 4096),
                "tags": data.get("tags", []),
                "ttl_seconds": data.get("ttl_seconds", 0),
                # 生成温度：None = 不干预执行器默认；给了值走 llm_factory 的温度包装
                "llm_temperature": _llm_temperature(data),
                # 角色（受控模板 + 分级开关）：未知模板 / 默认档配自由文本在此 400。
                # 展开在 try 内：_role_fields 抛 ValueError ⇒ 外层转 400（不建容器）。
                **_role_fields(data),
                # 记忆档位：未知档位 / scoped 缺三要素或 provider / 默认档配非空域或配额
                # ⇒ E_MEMORY_CONFIG 400（不建容器）
                **_memory_fields(data, provider=data.get("memory_provider", "")),
            }

            container = Yunshu.create_subagent(config)
            return jsonify({
                "ok": True,
                "subagent": container.get_status(),
                "message": f"分身 '{container.config.name}' 创建成功",
            })
        except MemoryConfigError as e:
            logger.warning("[SubagentAPI] 创建失败（记忆配置非法）: %s", e)
            return jsonify({"ok": False, "error_code": "E_MEMORY_CONFIG",
                            "error": str(e)}), 400
        except Exception as e:
            logger.error("[SubagentAPI] 创建失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/subagent/<name>/destroy", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_destroy(name):
        """销毁指定分身"""
        try:
            report = Yunshu.destroy_subagent(name)
            return jsonify({
                "ok": True,
                "report": report,
                "message": f"分身 '{name}' 已销毁",
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  执行 & 热更新
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/<name>/execute", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_execute(name):
        """在指定分身中执行任务

        **注意**：本端点走 ``SubagentContainer.execute()`` —— 设计上保持"占位骨架"
        （不调 LLM、不做委派协议），只回一段占位文案，供集成测试验证容器链路。
        需要**真执行**请用 ``POST /api/subagent/<name>/delegate``（同一容器，
        但走 DelegationExecutor 完整链路）。

        POST JSON:
            task (str): 任务描述
        """
        try:
            data = request.get_json() or {}
            task = data.get("task", "").strip()
            if not task:
                return jsonify({"ok": False, "error": "task 不能为空"}), 400

            result = Yunshu.execute_subagent(name, task)
            return jsonify({
                "ok": True,
                "name": name,
                "result": result,
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/<name>/delegate", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_delegate(name):
        """**真委派**：把任务交给指定分身，经真实执行器跑（八要素 + 隔离 + Trace + 成本）

        与 ``/execute``（容器占位骨架，不调 LLM）不同，本端点走
        ``SubagentContainer.run_delegation`` → ``DelegationExecutor``，
        与模型侧 ``delegate`` 工具同一条链路。

        POST JSON（八要素；UI 简化入口，未传的项按下方缺省补齐并随响应回显）：
            task            ①目标（必填，≥8 字符；含糊目标会被执行器拒绝）
            constraints     ②约束（列表，缺省 ["只读为主，不得修改仓库文件"]，不得为空列表）
            prior_artifacts ③已有成果（列表，缺省 []）
            prohibitions    ④禁止事项（列表，缺省 []；UI 额外默认补"不得对外发送数据"）
            artifact_format ⑤产物格式（缺省 "文本要点"）
            budget_tokens   ⑥预算令牌（缺省 4000）
            timeout_seconds ⑦超时秒数（缺省 120）
            callback_url    ⑧回调地址（缺省 ""，即同步返回）
        """
        try:
            data = request.get_json(silent=True) or {}
            task = _goal_from_body(data)
            if len(task) < 8:
                return _short_goal_response()

            # ── 分身解析：用**既有**生命周期管理器取容器（绝不 new 第二实例） ──
            mgr = getattr(Yunshu, "_subagent_mgr", None)
            if mgr is None or not callable(getattr(mgr, "get", None)):
                return jsonify({
                    "ok": False, "error_code": "E_SUBAGENT_UNAVAILABLE",
                    "error": "分身系统未启用（subagent.enabled=False 或核心系统未初始化）",
                }), 409
            container = mgr.get(name)
            if container is None:
                # 【提示去处】具名端点的 404 不再是"死路"：临时分身入口不要求先有分身
                return jsonify({
                    "ok": False, "error": f"分身不存在: {name}",
                    "hint": "可改用 POST /api/subagent/delegate（临时分身：现建现用、跑完即回收）",
                }), 404

            # ── 执行通道：LLM 优先；都没有则明确拒绝（不跑注定失败的空执行） ──
            channel = _channel_info(Yunshu)
            if not channel["ok"]:
                return _no_channel_response(channel)

            # ── 角色（受控模板）：按**这个分身的**配置解析；无效配置不得带病委派 ──
            # 档位与片段都来自 `agent/subagent/role_templates.py` 的受控词表：
            #   · 默认 template 档：system_prompt = 模板正文；role_text 绝不进 system prompt；
            #   · template+text 档：自由文本只进 ②约束（下面 extra_constraints）；
            #   · full-system 档：自由文本进 system_prompt（红档，执行器写审计）。
            cfg = getattr(container, "config", None)
            from agent.subagent.role_templates import resolve_subagent_role

            role = resolve_subagent_role(
                getattr(cfg, "role_template", ""), getattr(cfg, "role_text", ""),
                getattr(cfg, "role_mode", "template"))
            if role.error:
                # 进门时已校验过一次；这里再挡一次，防"配置是热更新改坏的"绕过创建校验
                return jsonify({"ok": False, "error_code": "E_ROLE_CONFIG",
                                "error": role.error}), 400

            # ── 记忆档位：同样再挡一次（配置可能被热更新改坏），无效不得带病委派 ──
            memory_plan = resolve_memory_config(
                getattr(cfg, "memory_mode", "none"), getattr(cfg, "memory_scope", None),
                getattr(cfg, "memory_provider", ""), getattr(cfg, "memory_quota", None))
            if memory_plan.error:
                return jsonify({"ok": False, "error_code": "E_MEMORY_CONFIG",
                                "error": memory_plan.error}), 400

            # ── 八要素（未传项按文档缺省补齐；校验在 DelegationExecutor 内继续生效） ──
            # 补齐规则与响应形状都由模块级 helper 提供：临时分身入口共用同一份
            try:
                ctx = _build_context(data, task, extra_constraints=role.constraints)
                # 二次派发：previous_delegation_id ⇒ 同 task_id 的第二轮（fail-closed）
                ctx, round_info = _apply_follow_up(data, ctx)
            except RoundError as e:
                return jsonify({"ok": False, "error_code": e.code, "error": str(e)}), 400
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400

            # 工具子集与模型侧 delegate 工具同源（同一只读白名单，避免两套授权口径）
            granted = _granted_tools()

            # ── LLM：按**这个分身自己的** model_id 解析（空 = 跟随母体）──
            # 【为什么在路由层解析】容器不该反向去找母体；而"母体 LLM"只有这里拿得到。
            # 解析结果**随响应回显**（llm.model / llm.source / llm.error）：
            # 指定模型未生效时必须看得见，绝不静默换模型（见 llm_factory 模块 docstring）。
            from agent.subagent.llm_factory import resolve_subagent_llm

            resolution = resolve_subagent_llm(
                getattr(getattr(container, "config", None), "model_id", ""),
                parent_llm=getattr(Yunshu, "_llm", None),
                temperature=getattr(getattr(container, "config", None), "llm_temperature", None))
            if resolution.source == "fallback-after-error":
                logger.warning("[SubagentAPI] 分身 %s 的指定模型未生效（已回退母体）: %s",
                               name, resolution.error)

            logger.info("[SubagentAPI] 真委派 name=%s delegation=%s goal=%.60s tools=%s llm=%s(%s) role=%s(%s)",
                        name, ctx.delegation_id, task, list(granted) or "（空）",
                        resolution.model or "?", resolution.source,
                        role.template or "未装", role.tier)
            outcome = container.run_delegation(
                ctx,
                llm=resolution.llm,
                tools=granted,
                authorized_capabilities=granted,
                # 角色片段只承载受控模板正文（full-system 档才含显式开启的自由文本）
                system_prompt=role.system_prompt,
                role_tier=role.tier,
                source="ui",  # 委派记录里区分"界面发起"与"模型工具发起"
            )
            payload = _outcome_payload(outcome, ctx)
            payload["name"] = name
            payload["elements"] = _elements_view(ctx)
            payload["channel"] = channel
            payload["llm"] = resolution.to_dict()
            # 角色生效情况随响应回显（模板/档位/红档/是否需审计；**不含 role_text 正文**）
            payload["role"] = role.to_dict()
            # 记忆配置随响应回显（档位/域/是否需审计；**不含记忆正文**）；
            # 实际取用结果在 outcome 映射的 payload["memory"] 里（有才回，含 degraded）
            payload["memory_config"] = memory_view(cfg)
            # 轮次回显（第几轮 / 跟的是谁）；首轮也有，恒为 round=1、follow_up=False
            payload["round"] = round_info
            return jsonify(payload)
        except Exception as e:
            logger.exception("[SubagentAPI] 委派失败: %s", e)
            return jsonify({"ok": False, "error_code": "E_DELEGATION_FAILED",
                            "error": str(e)}), 500

    @app.route("/api/subagent/delegate", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_delegate_ephemeral():
        """**临时分身委派**：不要求先存在分身 —— 与模型侧 ``delegate`` 工具同一条链路

        【为什么需要它】具名端点 ``/api/subagent/<name>/delegate`` 要求 ``mgr.get(name)``
        命中一个**存活容器**；而容器"跑完即回收"（``destroy_after=True``）⇒ 界面上长期是
        "列表为空 ⇒ 无法委托"，用户唯一的出路是先去「装配车间」手工造一个分身。
        本端点直接走 ``SubagentLifecycleManager.delegate``：**就地建临时分身 → 执行 →
        立即回收**，与模型工具、``fan_out`` 完全同一条执行链路（八要素 / 隔离 / Trace /
        成本记账 / 回收三件套），因此"一个分身都没有"时界面也能真委派。

        请求体与响应形状与具名端点**完全一致**（八要素缺省补齐复用同一份 helper），
        只是 URL 上不需要 ``name``；响应多一个 ``ephemeral: true`` 供界面区分。

        【回收纪律】``destroy_after=True`` 是硬编码的：临时分身不是资源、用完必须走
        （否则会撞 ``max_subagents`` 上限，且"临时"二字就成了谎话）。
        """
        try:
            data = request.get_json(silent=True) or {}
            task = _goal_from_body(data)
            if len(task) < 8:
                return _short_goal_response()

            # ── 生命周期管理器：必须是**既有**那一个（绝不 new 第二实例） ──
            mgr = getattr(Yunshu, "_subagent_mgr", None)
            if mgr is None or not callable(getattr(mgr, "delegate", None)):
                return jsonify({
                    "ok": False, "error_code": "E_SUBAGENT_UNAVAILABLE",
                    "error": "分身系统未启用（subagent.enabled=False 或核心系统未初始化）",
                }), 409

            channel = _channel_info(Yunshu)
            if not channel["ok"]:
                return _no_channel_response(channel)

            # 记忆档位（临时分身没有既有配置，直接读请求体；非法 ⇒ 400 E_MEMORY_CONFIG）
            # scoped 要求请求显式给出非空 memory_provider（fail-closed）
            mem_provider = str(data.get("memory_provider", "") or "")
            try:
                mem_fields = _memory_fields(data, provider=mem_provider)
            except MemoryConfigError as e:
                return jsonify({"ok": False, "error_code": "E_MEMORY_CONFIG",
                                "error": str(e)}), 400

            try:
                ctx = _build_context(data, task, delegation_prefix="dlg-ui-tmp")
                # 二次派发：与具名端点同一份 helper / 同一套 fail-closed 判定
                ctx, round_info = _apply_follow_up(data, ctx)
            except RoundError as e:
                return jsonify({"ok": False, "error_code": e.code, "error": str(e)}), 400
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400

            from agent.subagent.container import SubagentConfig
            from agent.tools.subagent_tools import _model_id

            llm = getattr(Yunshu, "_llm", None)
            granted = _granted_tools()
            # 名称只需给个前缀：重名消歧与 TTL（取契约⑦）由 lifecycle._prepare_config 统一负责，
            # 这里不重复实现 —— 否则又是一份会漂移的纪律
            config = SubagentConfig(name="ui-delegate", model_id=_model_id(llm),
                                    memory_provider=(mem_provider or "holographic"),
                                    **mem_fields)
            # 临时分身没有"用户选的模型"：config.model_id 抄的是母体当前模型，
            # 解析器把"指定的就是母体在用的那个"判为 inherit（不造无意义的影子实例）
            from agent.subagent.llm_factory import resolve_subagent_llm

            resolution = resolve_subagent_llm(config.model_id, parent_llm=llm)
            logger.info("[SubagentAPI] 临时分身委派 delegation=%s goal=%.60s tools=%s llm=%s(%s)",
                        ctx.delegation_id, task, list(granted) or "（空）",
                        resolution.model or "?", resolution.source)
            # 临时分身**没有**角色配置（当场现建、跑完即回收）：解析结果恒为"未装角色"
            # ⇒ system prompt 与改动前逐字相同。仍然走同一条解析与回显口径，
            # 免得"临时/具名"两条路径各有一套角色语义。
            from agent.subagent.role_templates import resolve_subagent_role

            role = resolve_subagent_role(config.role_template, config.role_text,
                                         config.role_mode)
            outcome = mgr.delegate(
                config, ctx, llm=resolution.llm, destroy_after=True,
                tools=granted, authorized_capabilities=granted,
                system_prompt=role.system_prompt, role_tier=role.tier, source="ui")

            payload = _outcome_payload(outcome, ctx)
            # 容器名由 lifecycle 生成消歧，界面只需知道"这不是用户选的具名分身"
            payload["name"] = "临时分身"
            payload["ephemeral"] = True
            payload["elements"] = _elements_view(ctx)
            payload["channel"] = channel
            payload["llm"] = resolution.to_dict()
            payload["role"] = role.to_dict()
            # 记忆配置回显（档位/域；**不含记忆正文**；实际取用结果见 payload["memory"]）
            payload["memory_config"] = memory_view(config)
            payload["round"] = round_info
            return jsonify(payload)
        except Exception as e:
            logger.exception("[SubagentAPI] 临时分身委派失败: %s", e)
            return jsonify({"ok": False, "error_code": "E_DELEGATION_FAILED",
                            "error": str(e)}), 500

    @app.route("/api/subagent/<name>/reload", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_reload(name):
        """热更新分身配置

        POST JSON:
            model_id (str, optional): 新模型 ID
            memory_provider (str, optional): 新记忆提供商
            tool_sources (list[str], optional): 新工具源
            permissions (list[str], optional): 新权限
            context_window (int, optional): 新上下文窗口大小
            ttl_seconds (int, optional): 新存活时间
            role_template / role_mode / role_text (optional): 角色三字段；
                未传则**沿用该分身现值**（含自由文本正文，故读的是存活容器的配置，
                不是状态投影 —— 状态投影刻意不含 role_text 正文）
            memory_mode / memory_scope / memory_quota (optional): 记忆档位、域与
                scoped 配额；未传则**沿用该分身现值**（读存活容器的配置）；
                非法 ⇒ 400 E_MEMORY_CONFIG
        """
        try:
            data = request.get_json() or {}

            # 获取当前配置作为基础
            current = Yunshu.get_subagent(name)
            if current is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404

            # 角色三字段未传时**沿用在世容器的现值**（不能读状态投影：它刻意不含
            # role_text 正文，从状态取值会把已有自由文本静默清空）
            role_base = _live_role_config(Yunshu, name)
            # 记忆两字段未传时同样沿用在世容器的现值（memory_scope 是标识，可安全回显）
            memory_base = _live_memory_config(Yunshu, name)
            new_config = {
                "name": name,
                "model_id": data.get("model_id", current["model_id"]),
                "memory_provider": data.get("memory_provider", current["memory_provider"]),
                "tool_sources": data.get("tool_sources", current["tool_sources"]),
                "permissions": data.get("permissions", current["permissions"]),
                "context_window": data.get("context_window", current["context_window"]),
                "tags": data.get("tags", current.get("tags", [])),
                "ttl_seconds": data.get("ttl_seconds", current.get("ttl_seconds", 0)),
                # 温度同款校验：越界即 400（不静默夹取）；未传则沿用现值
                "llm_temperature": _llm_temperature(data, current.get("llm_temperature")),
                # 角色同款校验：未知模板 / 默认档配自由文本在此 400（进门时挡，不留到运行时）
                **_role_fields(data, role_base),
                # 记忆同款校验：未知档位 / scoped 缺三要素或 provider / 默认档配非空域在此 400
                **_memory_fields(
                    data, memory_base,
                    provider=data.get("memory_provider",
                                      current.get("memory_provider", ""))),
            }

            Yunshu.hot_reload_subagent(name, new_config)
            updated = Yunshu.get_subagent(name)
            return jsonify({
                "ok": True,
                "subagent": updated,
                "message": f"分身 '{name}' 热更新完成",
            })
        except MemoryConfigError as e:
            return jsonify({"ok": False, "error_code": "E_MEMORY_CONFIG",
                            "error": str(e)}), 400
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
