"""统一调用入口 —— HTTP / CLI / 模型三条链路**同一个执行收口**（v1.4 §5.3）

## 铁律（E3，违反即验收不通过）

> `POST /capabilities/invoke` **必须**复用 `agent/tools/__init__.py::call()`
> 作为唯一执行收口 —— 这样工具闸门、审批、审计、限流**全部生效**；
> **不得**绕过 `tool_gate`。

本模块因此把"本地能力的执行"**唯一地**指向 `agent/tools/__init__.py::call()`：
`LocalLoader._do_invoke` 就是 `_tools.call(...)`（见 `loader.py`），
不存在第二条本地执行路径。

## 非交互场景的审批处置（方案 A，TASK-05 §3 第 4 步第 6 项）

**已证实的缺陷**：cron/CI 调用高危工具 ⇒ `tool_gate` 一律返回
`APPROVAL_REQUIRED` 并**挂单** ⇒ 非交互调用方**永远拿不到结果**，而且收件箱里
堆着一张永远不会被批准的单子（"悬空挂单"）。

**方案 A（本模块实现）**：当调用身份是**非交互**的
（`system` / `service_account`）且闸门要求审批时：

- **不挂空单**（`tool_gate` 那一步仍会挂单 —— 这是共享代码的行为，本任务不越权改它；
  见下方"为什么不去改 tool_gate"），
- 返回**明确的错误码 `denied`** + **可操作说明**（告诉调用方两条出路）。

**为什么不去改 `tool_gate.py` 让它不挂单**：
    ① `tool_gate` 是"所有调用方共享的必经路径"，改它等于改全平台行为（D2/D4 风险）；
    ② 挂单本身对**交互**场景是正确行为（人在收件箱里点一下就能放行）；
    ③ 非交互调用要的是"**明确失败**而不是"静默挂单后超时"，这个语义
       完全可以、也应该在**入口层**（本模块）表达。
    ⇒ 因此本模块是"在闸门之上加一层非交互语义"，而不是"改闸门"。
    这也是 `EXEMPT_CALL_SITES` 之外的一条**独立防线**：即使某天有人绕过了本入口，
    `tool_gate` 的挂单行为仍然存在，不会因此变得更糟。

方案 B（`service_account` 预授权，v1.4 §10.2）按任务书要求**只做接口预留**：
`PreAuthorizationHook` 是可注入的判定点，完整实现属 `TASK-06`。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .errors import (CapabilityError, CapabilityResult, CODE_META, err_result,
                     from_exception, ok_result)
from .loader import Handle, Loader, get_loader_manager
from .view import CapabilityRegistry, get_registry

logger = logging.getLogger(__name__)

__all__ = [
    "IDENTITY_LLM",
    "IDENTITY_HUMAN",
    "IDENTITY_SYSTEM",
    "IDENTITY_SERVICE_ACCOUNT",
    "IDENTITIES",
    "IDENTITY_SESSION_SOURCE",
    "invoke_capability",
    "invoke_envelope",
    "set_preauthorization_hook",
    "preauthorization_hook",
    "identity_propagating_executor",
]

IDENTITY_LLM = "llm"
IDENTITY_HUMAN = "human"
IDENTITY_SYSTEM = "system"
IDENTITY_SERVICE_ACCOUNT = "service_account"
IDENTITIES: Tuple[str, ...] = (IDENTITY_LLM, IDENTITY_HUMAN,
                               IDENTITY_SYSTEM, IDENTITY_SERVICE_ACCOUNT)

#: 入口身份 → `tool_gate` 的 `session_source`（v1.4 §5.1 与仓库 `trigger` 的桥）
#: 【为什么需要这层映射】`tool_gate` 的 `session_source` 是**来源**语义
#: （cli/web/api/scheduled），而 `callable_by` 是**身份**语义。两者不能直接等同，
#: 但必须有稳定的对应关系，否则"身份"永远落不到闸门上（这正是 `async_executor`
#: 的 `submit()` 缺身份参数的后果）。
IDENTITY_SESSION_SOURCE: Dict[str, str] = {
    IDENTITY_LLM: "api",            # 模型调用来自 API 面
    IDENTITY_HUMAN: "cli",          # 人（CLI/UI）调用
    IDENTITY_SYSTEM: "scheduled",   # 后台/定时任务
    IDENTITY_SERVICE_ACCOUNT: "api",  # 服务账号（CI）—— ABAC 可按来源区分
}

#: 非交互身份（方案 A 的适用范围）
_NON_INTERACTIVE: frozenset = frozenset({IDENTITY_SYSTEM, IDENTITY_SERVICE_ACCOUNT})


def _err(code: str, message: str, *, detail: str = "",
         capability: str = "") -> CapabilityError:
    """构造**我们自己撰写**的错误（`detail` 缺省取 `message`）

    ## 为什么需要这个helper（这是一个真实的 bug 修出来的）

    `errors.to_llm_safe()` 的 `message` 恒为**固定描述**（`CODE_META[code].llm_hint`）
    —— 这是"零泄漏风险"的结构保证；**只有 `detail` 会被脱敏后带给调用方**。
    于是若调用点把"可操作说明"写在 `message` 里、`detail` 留空，
    调用方（含 LLM）就**只看得到一句通用描述**，看不到出路。
    实测踩到：非交互审批的"请改由人工身份执行，或配置服务账号预授权"整段说明
    被丢掉了 —— 那正是"给出明确错误码 + **可操作说明**"（方案 A）的一半没落地。

    ## 安全边界（**不要滥用**）

    本 helper 只用于**我们自己撰写**的文案：`message` 里不含任何异常原文、路径、
    URL（那些只在 `errors.from_exception()` 的 `detail` 里）。把异常原文交给本
    helper 就等于把"未脱敏文本"塞进会被透出的字段 —— 仍然会被 `redact()` 清洗，
    但**不要依赖它**：判据是"这句话是不是我写的"。
    """
    return CapabilityError(code, message, detail=detail or message,
                           capability=capability)


def _approval_message_enabled() -> bool:
    """非交互审批处置开关（默认 1 = 启用方案 A）"""
    raw = str(os.environ.get("CP_CAPABILITY_NONINTERACTIVE_APPROVAL", "1") or "1")
    return raw.strip().lower() not in ("0", "false", "no", "off")


# ── 方案 B 的接口预留（TASK-06 落地）──

_HOOK: Dict[str, Any] = {"fn": None}


def set_preauthorization_hook(fn: Optional[Callable[..., Any]]) -> None:
    """注册 `service_account` 预授权判定钩子（**接口预留**，TASK-06 实现）

    钩子签名：`fn(capability: str, identity: str, args: dict) -> bool`
    返回 True 表示该 SA 已对该能力预授权（可放行审批边界）。
    """
    _HOOK["fn"] = fn


def preauthorization_hook() -> Optional[Callable[..., Any]]:
    return _HOOK["fn"]


def identity_propagating_executor(identity: str = IDENTITY_LLM,
                                  session_source: str = "",
                                  *, tool_call: Optional[Callable[..., Any]] = None
                                  ) -> Callable[..., Any]:
    """构造一个**如实上报身份**的工具执行器（`(tool_name, params) -> result`）

    ## 为什么需要它（这是 TASK-06 §3 第 5 步"4 条身份绕过面"里的面 3）

    实测两处把 `agent.tools.call` 包成**裸 lambda** 注进工作流学习服务：

      · `agent/orchestrator/orchestrator.py:1990`
        `lambda tool_name, params: _tool_call(tool_name, **params)`
      · `agent/server_routes/routes_workflow_learning.py:29`（同上写法）

    裸 lambda **没有声明任何身份** ⇒ 工作流回放真的会执行工具（`@require_token` 只管
    "能不能进这个端点"，不管"以谁的身份执行工具"），但工具侧看到的
    `session_source` 会落到处环境变量缺省值 `"cli"`（= 一次 HTTP 触发的回放被记成
    "人从 CLI 调的"），`execution_identity` 则是空串。

    本函数把"调用时**当场**读出当前上下文身份 → 在执行器体内如实设置"这件事收口成
    一个可复用工厂。**参数 `identity` 是缺省值而不是覆盖值**：优先用当前上下文的
    身份，上下文没有才用参数，最后兜底 `llm`。理由是工作流回放的**真实**触发者可能
    是人（在对话里触发）或 SA（CI 触发）；用参数覆盖上下文会把真实触发者抹成调用方
    写死的那个 —— 那正是"身份归因错误"本身。

    ## 与 `_NON_INTERACTIVE` 的关系

    身份如实上报之后，`tool_gate` 的"非交互 ⇒ 直接拒绝（不挂单）"与
    "无身份 ⇒ 拒绝"两条判定才**对这条链路生效**。裸 lambda 时代它两条都躲过了。

    ## 线程语义（**关键**）

    `set_session_source` / `set_execution_identity` 用 contextvars，**不跨线程继承**。
    本执行器在**被调用的那个线程内**设置（而不是在构造时设置），
    因此无论工作流引擎在哪个线程里回调它，设置点都正确。
    """
    def _exec(tool_name: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        call = tool_call
        if call is None:
            from agent.tools import call as call  # noqa: PLC0415 惰性：避免导入期成环
        ident = str(identity or "").strip().lower() or None
        src = str(session_source or "").strip()
        id_handle = None
        src_handle = None
        try:
            from agent.tool_gate import (  # noqa: PLC0415
                current_execution_identity, current_session_source,
                set_execution_identity, set_session_source)
            # 【顺序：上下文优先，参数兜底】`identity` 是**缺省值**而不是覆盖值。
            # 理由：工作流回放的真实触发者可能是 human（人在对话里触发）或
            # service_account（CI 触发）；若参数覆盖上下文，就会把真实触发者
            # 抹成调用方写死的那个 —— 那正是"身份归因错误"本身。
            eff_ident = current_execution_identity() or ident or IDENTITY_LLM
            eff_src = (current_session_source() or src
                       or IDENTITY_SESSION_SOURCE.get(eff_ident, ""))
            if eff_ident:
                id_handle = set_execution_identity(eff_ident)
            if eff_src:
                src_handle = set_session_source(eff_src)
        except Exception as exc:  # noqa: BLE001  闸门不可用 ⇒ 身份标注降级（不影响调用）
            logger.warning("[capregistry] 执行器身份上报不可用（按未声明处理）: %s: %s",
                           type(exc).__name__, exc)
        try:
            return call(tool_name, **dict(params or {}))
        finally:
            for h in (src_handle, id_handle):
                if h is not None:
                    try:
                        h.reset()
                    except Exception:  # noqa: BLE001
                        pass
    return _exec


def _preauthorized(capability: str, identity: str,
                   args: Mapping[str, Any]) -> bool:
    """查询 SA 预授权（无钩子 / 钩子异常 ⇒ 视为**未**预授权，绝不静默放行）"""
    fn = _HOOK["fn"]
    if fn is None:
        return False
    try:
        return bool(fn(capability, identity, dict(args)))
    except Exception as exc:  # noqa: BLE001  预授权查询失败 ⇒ 按未授权（fail-closed）
        logger.warning("[capregistry] 预授权钩子异常（按未授权处理）: %s", exc)
        return False


# ════════════════════════════════════════════════════════════
#  参数契约校验
# ════════════════════════════════════════════════════════════


def _validate_input(spec: Any, args: Mapping[str, Any]) -> Optional[CapabilityError]:
    """`input_schema` 校验（只在**有** schema 时执行；无 schema ⇒ 放行）

    【为什么无 schema 时放行而不是拒绝】114 条能力里 22 条 `schema_registered=false`
    （纯提示词技能与内部执行体）。对它们做"无契约即拒绝"会把存量能力一刀砍掉，
    与 D2 冲突。**如实披露**由 `/capabilities/tools` 的 `schema_registered` 字段承担。
    """
    schema = getattr(spec, "input_schema", None)
    if not isinstance(schema, dict) or not schema:
        return None
    from .contract import validate_against_schema  # noqa: PLC0415 避免环
    ok, errors, _validator = validate_against_schema(dict(args), schema)
    if ok:
        return None
    return CapabilityError(
        "schema_error",
        f"入参不符合 {getattr(spec, 'tool_name', '?')} 的 input_schema",
        detail="; ".join(errors[:5]),
        capability=str(getattr(spec, "tool_name", "")))


# ════════════════════════════════════════════════════════════
#  统一调用
# ════════════════════════════════════════════════════════════


def _denied_from_gate(result: Any, *, capability: str,
                      identity: str) -> CapabilityError:
    """把 `tool_gate` 的拒绝结构翻译成统一错误码（**方案 A 的落点**）

    闸门返回的是 **dict**（不是异常），形状见 `agent/tool_gate.py::_deny*`：
    `{"ok": False, "error": ..., "error_code": "APPROVAL_REQUIRED"|"PERMISSION_DENIED"
      |"APPROVAL_REJECTED", "approval_id": ..., "guidance": ...}`（字段因分支而异）。
    """
    code_raw = str((result or {}).get("error_code") or "").upper()
    reason = str((result or {}).get("error") or "调用被治理策略拒绝")
    approval_id = str((result or {}).get("approval_id") or "")

    if code_raw == "APPROVAL_REQUIRED" and identity in _NON_INTERACTIVE \
            and _approval_message_enabled():
        # ── 方案 A：非交互来源**不返回悬挂单号**，而是明确失败 + 出路 ──
        guidance = (
            f"本次调用的身份是 {identity!r}（非交互），审批边界要求人工确认。"
            "非交互来源下该调用**不会**产生待办（避免悬空挂单），"
            "请二选一：① 改由人工身份（CLI 交互 / 审批收件箱）执行一次；"
            "② 为服务账号配置预授权后重试（v1.4 §10.2，方案 B，接口已预留 "
            "set_preauthorization_hook()）。"
        ) if not approval_id else (
            f"本次调用的身份是 {identity!r}（非交互）。审批单 {approval_id} 已存在，"
            "但非交互来源无法在收件箱完成裁决 ⇒ 视为不可用。"
            "请改由人工身份执行，或为服务账号配置预授权。"
        )
        return _err("denied", f"{reason}；{guidance}", capability=capability)
    return _err(
        "denied",
        reason if code_raw != "APPROVAL_REJECTED" else f"人工已否决：{reason}",
        detail=f"{code_raw}；{reason}", capability=capability)


def _gate_rejected(result: Any) -> bool:
    """闸门返回的 dict 是否表示**拒绝**（而非正常工具结果）

    判据取"显式 `ok is False` 且有 `error_code`"：`tool_gate` 的拒绝结构一律带
    `error_code`，而真实工具结果**极少**同时满足这两条（实测 91 个工具的返回
    结构里没有 `error_code` 键；它们用 `error` 字符串）。
    """
    return isinstance(result, dict) and result.get("ok") is False \
        and bool(result.get("error_code"))


def invoke_capability(name: str, args: Optional[Mapping[str, Any]] = None, *,
                      identity: str = IDENTITY_HUMAN,
                      tenant_id: str = "default",
                      version: str = "",
                      registry: Optional[CapabilityRegistry] = None,
                      loader_manager: Any = None) -> CapabilityResult:
    """**唯一**的能力调用实现（HTTP / CLI / 模型三条链路共用）

    流程（每一步都对应一条验收项）：

    1. 身份合法性 + `callable_by` 白名单（v1.4 §5.1）；
    2. Registry 主键查询（`not_found` 若不存在）；
    3. `input_schema` 校验（`schema_error`）；
    4. 选 Loader（`location` 分叉）+ `resolve` + `connect`；
    5. **本地** ⇒ `agent/tools/__init__.py::call()`（闸门/限流/审计全生效）；
       **远端** ⇒ 对应 Loader 的真实传输；
    6. 闸门拒绝 ⇒ 统一错误码（含**方案 A** 的非交互处置）；
    7. `result_schema` 校验（`contract.py`）—— 契约违约**不**改变 `status`
       （执行确实成功了），但会出现在 `meta.contract` 里供 CI 判定；
    8. 异常 ⇒ `errors.from_exception()` 唯一映射 ⇒ **绝不让原文进 LLM 上下文**。

    ⚠️ 返回值 `meta.timing` 是**唯一**的易变字段（HTTP 与 CLI 对拍时按契约忽略它）。
    """
    started = time.perf_counter()
    identity = str(identity or IDENTITY_HUMAN).strip().lower() or IDENTITY_HUMAN
    if identity not in IDENTITIES:
        return err_result(CapabilityError(
            "validation_error",
            f"未知身份 {identity!r}（值域：{', '.join(IDENTITIES)}）"))

    reg = registry if registry is not None else get_registry()
    spec = reg.get(name, tenant_id=tenant_id)
    if spec is None:
        return err_result(_err(
            "not_found", f"能力 {name!r} 不存在或未注册", capability=name))

    meta_base: Dict[str, Any] = {
        "tool_name": spec.tool_name,
        "capability_id": spec.capability_id,
        "kind": spec.kind,
        "location": spec.location,
        "version": spec.version,
        "tenant_id": spec.tenant_id,
        "registry_source": spec.registry_source,
        "callable_by": list(spec.callable_by),
        "impl_status": spec.impl_status,
        "degraded": reg.degraded,
        "loader": "",
        "contract": "undeclared",
        "contract_errors": [],
    }

    # ① 入口身份白名单（**独立于闸门**：即使闸门关闭，身份白名单仍然生效）
    if identity not in spec.callable_by:
        return err_result(_err(
            "denied",
            f"身份 {identity!r} 不在 {spec.tool_name!r} 的 callable_by 白名单内"
            f"（允许：{', '.join(spec.callable_by) or '无'}）",
            capability=spec.tool_name), meta_base)

    if version and str(spec.version) != str(version):
        return err_result(_err(
            "not_found",
            f"能力 {spec.tool_name!r} 的版本 {spec.version!r} 与请求的 {version!r} 不符",
            capability=spec.tool_name), meta_base)

    # ③ 入参契约
    verr = _validate_input(spec, args or {})
    if verr is not None:
        return err_result(verr, meta_base)

    # ④ Loader 分叉
    mgr = loader_manager if loader_manager is not None else get_loader_manager()
    loader: Optional[Loader] = mgr.loader_for(spec) if mgr is not None else None
    if loader is None:
        return err_result(CapabilityError(
            "unhealthy",
            f"能力 {spec.tool_name!r}（location={spec.location}）没有可用的 Loader",
            capability=spec.tool_name), meta_base)
    meta_base["loader"] = loader.kind
    handle: Handle = loader.resolve(spec)
    # `connect()` 是**幂等**的（子类实现只保证"池子里有连接"），且退避期内会
    # 直接返回 False —— 于是"退避中不发起真实调用"这条语义只在 Loader 里实现一次。
    if not loader.connect(handle):
        return err_result(CapabilityError(
            "unhealthy",
            f"Loader {loader.kind!r} 连接失败：{loader.last_error or '未知原因'}",
            capability=spec.tool_name), meta_base)

    # ⑤ 执行（本地一定会落到 tools.call()；见模块 docstring）
    session_source = IDENTITY_SESSION_SOURCE.get(identity, "")
    preauthorized = (_preauthorized(spec.tool_name, identity, args or {})
                     if identity == IDENTITY_SERVICE_ACCOUNT else False)
    try:
        # 【身份透传】把入口身份写进 tool_gate 的上下文变量 —— 这是
        # `async_executor.submit()` 缺身份那条链路的**修正范式**（见 call_sites.py）。
        from agent.tool_gate import set_session_source  # noqa: PLC0415 惰性
        handle_ctx = set_session_source(session_source) if session_source else None
    except Exception:  # noqa: BLE001  闸门不可用时身份透传降级（不影响调用）
        handle_ctx = None
    try:
        outcome = loader.invoke(handle, args or {})
    finally:
        if handle_ctx is not None:
            try:
                handle_ctx.reset()
            except Exception:  # noqa: BLE001
                pass

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    meta_base["timing"] = {"duration_ms": elapsed_ms}
    meta_base["preauthorized"] = preauthorized

    if not outcome.ok:
        # 【不易·为什么用 `from_exception` 而不是直接采信 `outcome.code`】
        #   Loader 只知道"传输/执行环节失败了"，一律给 `unhealthy`。
        #   但"未知工具"（应归 `not_found`）与"工具执行炸了"（应归 `internal_error`）
        #   对调用方是**完全不同的处置**（前者要改能力名，后者要重试/查日志）。
        #   把原始异常交给**唯一映射点** `errors.from_exception()` 才能区分开。
        if outcome.exception is not None:
            err = from_exception(outcome.exception, capability=spec.tool_name)
            if not err.detail:
                err.detail = outcome.detail
        else:
            err = CapabilityError(outcome.code or "internal_error", "",
                                  detail=outcome.detail,
                                  capability=spec.tool_name)
        return err_result(err, meta_base)

    data = outcome.data

    # ⑥ 闸门拒绝的统一翻译（本地路径下闸门返回 dict，不会抛异常）
    if loader.kind == "local" and _gate_rejected(data):
        return err_result(_denied_from_gate(data, capability=spec.tool_name,
                                            identity=identity), meta_base)

    # ⑦ result_schema 校验（不改 status：执行确实成功）
    from .contract import result_status  # noqa: PLC0415 避免环
    rs = result_status(spec, data)
    meta_base["contract"] = rs["contract"]
    meta_base["contract_errors"] = rs["contract_errors"]
    meta_base["result_status"] = rs["status"]
    return ok_result(data, meta_base)


def invoke_envelope(name: str, args: Optional[Mapping[str, Any]] = None,
                    **kw: Any) -> Dict[str, Any]:
    """`invoke_capability` 的 JSON 信封（HTTP 与 CLI **共用同一份**）"""
    return invoke_capability(name, args, **kw).to_dict()
