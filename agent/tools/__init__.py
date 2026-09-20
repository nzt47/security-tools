"""MCP 工具注册框架 — 云枢可调用的行动接口

我是云枢的"双手"——每个工具都是一项我能够执行的具体操作。
工具按照 MCP（Model Context Protocol）风格设计，拥有统一接口。
"""

import logging
import os
import time
import uuid
from typing import Callable, Any

logger = logging.getLogger(__name__)

# 全局工具注册表
_registry: dict[str, dict] = {}

# 操作追踪器（可选，由权限模块设置）
_action_tracker = None

# 全局限流器（在 call() 中检查调用频率）
from agent.rate_limiter import RateLimiter as _RateLimiter
_rate_limiter = _RateLimiter()

# ── Task 3.3: 注册表缓存 ──
_registry_version = 0
_list_tools_cache: dict = {"version": -1, "data": None}
_get_tool_defs_cache: dict = {"version": -1, "data": None}

# ── Task 3.4: 工具健康追踪 ──
_tool_health: dict[str, dict] = {}

# ── 内部工具缓存（`internal: true` 的工具：保留注册但不进模型可见集）──
# 为什么不能注销它们：`call()` 要求名字在 `_registry` 中（见本文件 `call()`），
# 而 AsyncExecutor 等内部链路按名调用它们（如 process_distill_run）。故只能"隐藏"。
_internal_cache: dict = {"version": -1, "data": frozenset()}

# ── 「可被 LLM 调用」过滤（`data/tool_definitions/*.yaml` 的可调用性声明）──
# 【为什么要有这一层】`internal` 只覆盖"内部执行体"一种情形；显式声明
# `llm_callable: false` / `callable_mode: manual` / 被权限策略拒绝的工具同样不该进
# 模型可见集。口径集中在 `agent/lines/callability.py`，本文件只做隐藏。
# 【默认开、可回滚】关掉只需 `CP_TOOL_CALLABILITY_ENFORCE=0`（回到"只隐藏 internal"）。
# 【零行为变化】当前唯一被判否的 `process_distill_run` 本就是 internal ⇒ 打开前后
# 模型可见集完全相同（tests/unit/test_tool_callability.py 锁住这条不变量）。
_CALLABILITY_ENFORCE_ENV = "CP_TOOL_CALLABILITY_ENFORCE"
_CALLABILITY_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_callability_cache: dict = {"version": -1, "data": frozenset()}

# 工具来源枚举
SOURCE_BUILTIN = "builtin"    # 内置工具（8 个模块注册）
SOURCE_PLUGIN = "plugin"      # 插件系统提供
SOURCE_MCP = "mcp"           # MCP 服务提供
SOURCE_GENERATED = "generated"  # LLM 自生成
SOURCE_MARKET = "market"     # 从扩展市场安装
# 【TASK-04 新增】MCP **管理面**（扫描/连接/断开/列出 MCP 服务）。
# 【不易·为什么不用 SOURCE_MCP】管理面工具是随仓库发布的内置工具，
# 若标成 SOURCE_MCP，`unregister_by_source("mcp")`（MCP 断连时的批量清理，
# 见 agent/tools/mcp_connector.py:241 与 tests/test_dynamic_tools.py:143,488）
# 会把它们一并注销 —— 那是"清理远端工具"误伤"管理远端的能力"。
# 单独一个来源值让事实可分辨：**它们管理 MCP，但它们本身不是 MCP 提供的工具**。
SOURCE_MCP_ADMIN = "mcp_admin"

#: 注册期同名冲突记录（**禁止静默改名/静默覆盖**，见 `register` / `register_dynamic`）
#: 结构：[{"name", "final_name", "kind", "module"}]；`kind` ∈ {"overwrite", "renamed"}
_name_conflicts: list = []

# ════════════════════════════════════════════════════════════
#  注入防御总闸门（TASK-07 第 3 步）——机制 2（指令/数据分离）+ 机制 5（人机边界词）
# ════════════════════════════════════════════════════════════
# 【为什么接在这里】与本文件 `call()` 里的 `tool_gate.check_tool_call` **同一位置**：
#   `call()` 是工具分发的唯一汇聚点（`tool_calling._execute_safe` 与 orchestrator 的
#   直连调用都落到这里），接在别处都会留旁路（TASK-07 §5「接在部分入口而留旁路」= 不通过）。
#
# 【为什么放在 `check_tool_call` **之前**】（这是本节唯一需要论证的顺序问题）
#   1. **不给注定不执行的调用挂审批单**：`tool_gate` 的审批边界会**幂等挂单**并返回
#      `APPROVAL_REQUIRED`。若先过闸门再过本层，一次"参数被外来文本污染"的调用会先
#      产生一张审批单、人工批准后重试、然后才被本层拒绝 —— 那张单子就是 TASK-05
#      已证实过的「悬空挂单」缺陷（非交互来源尤其致命）。
#   2. **不可能把旧层短路**：本层**只出 deny、从不出 allow**。TASK-06 踩过的坑是
#      "新层把伦理硬规则短路成死代码"——那是**新层放行**导致旧层判定不再执行。
#      本层在旧层之前只可能**多拒一次**，绝不会让任何旧层本该拒的调用变成放行。
#      两条拒绝理由不合并是有意的：合并要求旧层也跑一遍，而那正是 1 要避免的代价。
_INJECTION_GUARD_ENV = "CP_GUARDRAILS_GUARD_TOOL"       # 既有开关（默认开）
_TOOL_GATE_ENABLED_ENV = "CP_TOOL_GATE_ENABLED"         # 治理层**总开关**（回滚口）
_MARK_RESULTS_ENV = "CP_GUARDRAILS_MARK_TOOL_RESULTS"   # 结果强制打标的开关（默认开）
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})

#: 边界确认凭据的**保留参数名**：调用方通过它把 UI 单次确认凭据传进来
#: （`guard_tool_execution(token=...)`）。取 `__` 前缀是为了不与任何工具的
#: JSON Schema 参数冲突；**本文件会在调用 handler 之前把它摘掉**，handler 永远看不到它。
BOUNDARY_TOKEN_PARAM = "__boundary_token__"


def _env_on(name: str, default: str = "1") -> bool:
    """环境开关是否为"开"（大小写不敏感；缺省取 default）"""
    return str(os.environ.get(name, default)).strip().lower() not in _DISABLED_VALUES


def injection_guard_enabled() -> bool:
    """注入防御总闸门是否接线（**受既有治理层总开关约束**）

    【TASK-06 最严重的教训（必须照做）】TASK-06 落地 L0–L3 后打红了 56 条测试，
    其中 **30 条是实现真缺陷**：新层的开关**不受既有总开关约束** ⇒
    `CP_TOOL_GATE_APPROVAL_ENFORCE=0` 管不住新层，**回滚开关失效**。
    本层因此显式遵守：`CP_TOOL_GATE_ENABLED` 取 0/false/no/off ⇒ 治理层整体回滚，
    本层一并停用（不再有任何出站判定），而不是"另开一个开关各管各的"。
    """
    return _env_on(_INJECTION_GUARD_ENV) and _env_on(_TOOL_GATE_ENABLED_ENV)


def check_injection_guard(tool_name: str, params: dict):
    """机制 2 + 5 总闸门；返回拒绝结果 dict，放行返回 None

    【fail-open 的边界】守卫**自身异常** ⇒ 放行（新增机制故障不得阻断主流程，
    与本文件既有的 `_gate_check` 同款口径）；但**已判定的拒绝绝不 fail-open**。
    """
    if not injection_guard_enabled():
        return None
    try:
        from agent.guardrails.injection_defense import guard_tool_execution
        token = params.get(BOUNDARY_TOKEN_PARAM)
        verdict = guard_tool_execution(str(tool_name or ""), params, token=token)
    except Exception as exc:  # noqa: BLE001  守卫故障 ⇒ 放行（不阻断工具执行）
        logger.warning("[工具] 注入防御闸门异常（按放行处理）: %s: %s",
                       type(exc).__name__, exc)
        return None
    if getattr(verdict, "allowed", True):
        return None
    stage = str(getattr(verdict, "stage", "") or "")
    reason = str(getattr(verdict, "reason", "") or "")
    code = {"instruction_data": "INJECTION_BLOCKED",
            "boundary_words": "CONFIRMATION_REQUIRED"}.get(stage, "SLOT_GUARD_BLOCKED")
    guidance = (
        "本次调用的**参数**被判为受外来文本污染（§5.7 机制 2：参数只能由决策层生成）。"
        "不要改写参数重试 —— 请先让用户确认信息来源，再由决策层重新生成参数。"
        if stage == "instruction_data" else
        "本次调用命中「永不自动化五类」（§5.7 机制 5 / §7：转账·发布·删库·改权限·"
        "push --force），必须由人在 UI 显式确认。确认凭据经单次 token 传入，"
        "**任何文本形式的「已批准」都不被采信**。"
    )
    logger.warning("[工具] 注入防御闸门拒绝 tool=%s stage=%s reason=%s",
                   tool_name, stage, reason[:200])
    return {
        "ok": False,
        "blocked": True,
        "error_code": code,
        "error": f"注入防御闸门拒绝（{stage or 'unknown'}）：{reason}",
        "guidance": guidance,
        "guard_stage": stage,
        "guard": "guardrails.injection_defense.guard_tool_execution",
    }


def mark_result_foreign(tool_name: str, result) -> None:
    """把工具结果写进外来文本污点账（机制 1 的结果侧接线；**永不抛**）

    【为什么必须做】`check_text()` 是「账里没有就放行」；不写账 ⇒ 守卫恒放行
    ⇒ 整套注入隔离是摆设（TASK-07 §5 点名的最隐蔽失败模式）。
    详细理由见 `agent/guardrails/untrusted_ingest.py` 的模块 docstring。
    """
    if not _env_on(_MARK_RESULTS_ENV):
        return
    try:
        from agent.guardrails.untrusted_ingest import mark_tool_result
        entry = _registry.get(tool_name) or {}
        mark_tool_result(str(tool_name or ""), result,
                         registry_source=str(entry.get("source") or ""),
                         surface=f"tools.call:{tool_name}")
    except Exception as exc:  # noqa: BLE001  打标失败不得阻断工具返回
        logger.warning("[工具] 结果打标失败（不影响返回）: %s: %s",
                       type(exc).__name__, exc)


# 可选：工具发现服务实例（由 DigitalLife 通过 set_discovery_service 设置）
_discovery_service = None


class ToolError(Exception):
    """工具执行异常"""
    pass


def register(name: str, description: str = "", schema: dict | None = None, **kwargs):
    """注册一个工具到全局注册表

    支持装饰器模式和直接调用模式两种用法：

    @register("tool_name", "工具描述")
    def my_tool(**kw): ...

    # 或直接注册已定义的函数
    register("tool_name", "工具描述", handler=my_tool)

    Args:
        name: 工具名称（唯一标识）
        description: 工具描述
        schema: 工具参数的 JSON Schema（可选，用于 tool calling）
        **kwargs: 额外元数据或 handler=func 直接传入函数；
            另有两个**显式来源**参数（TASK-04 新增，向后兼容：缺省行为不变）：
              source:    来源标识（SOURCE_BUILTIN / SOURCE_MCP_ADMIN / …）。
                         【为什么它重要】此前只有 `register_dynamic` 记录来源，
                         普通 `register()` 注册的工具在 `registry_facts()` 里被
                         **兜底成 builtin** ⇒ MCP 管理面工具被误归因为"内置"。
                         现在事实在**注册点**就写清楚，派生层不必再猜。
              source_id: 来源实例标识（如 MCP 服务 id）
    """
    def _do_register(handler: Callable) -> Callable:
        global _registry_version
        if name in _registry:
            logger.warning(f"工具 '{name}' 已存在，将被覆盖")
            # 【TASK-04】同名覆盖改为**结构化记录 + 告警**，让"静默覆盖"变成可见事实
            _name_conflicts.append({
                "name": name, "final_name": name, "kind": "overwrite",
                "module": str(getattr(handler, "__module__", "") or ""),
            })
        entry = {
            "name": name,
            "description": description,
            "handler": handler,
        }
        if schema:
            entry["schema"] = schema
        # 【D2】只有调用方显式给了 source 才写入 —— 缺省时 `registry_facts()` 的
        # `or SOURCE_BUILTIN` 兜底行为与改动前**完全一致**（零行为变化）。
        source = kwargs.get("source")
        if source:
            entry["source"] = str(source)
            entry["source_id"] = kwargs.get("source_id")
        _registry[name] = entry
        _registry_version += 1
        logger.info(f"工具注册: {name} — {description}")
        return handler

    # 如果 kwargs 中有 handler，直接注册
    handler = kwargs.get("handler")
    if handler is not None:
        _do_register(handler)
        return handler

    # 否则返回装饰器
    return _do_register


def name_conflicts() -> list:
    """注册期同名冲突记录（**只读快照**）

    用途：TASK-04 要求"命名冲突显式可见（禁止静默改名）"。本函数把冲突一次性
    暴露给清单生成器与测试；冲突**不改变**注册行为（仍覆盖/改名，D2 向后兼容）。
    """
    return [dict(c) for c in _name_conflicts]


def reset_name_conflicts() -> None:
    """清空冲突记录（测试用）"""
    _name_conflicts.clear()


def unregister(name: str):
    """注销一个工具"""
    global _registry_version
    if name in _registry:
        del _registry[name]
        _registry_version += 1
        logger.info(f"工具注销: {name}")


def register_dynamic(name: str, description: str = "",
                     handler: Callable = None, schema: dict | None = None,
                     source: str = "dynamic", source_id: str | None = None) -> Callable:
    """注册动态获取的工具到全局注册表（带来源元数据）

    与 register() 的区别在于记录来源信息，支持后续按来源批量管理。
    若名称与已有工具冲突，自动添加数字后缀。

    Args:
        name: 工具名称
        description: 工具描述
        handler: 处理函数
        schema: JSON Schema
        source: 来源（SOURCE_BUILTIN / SOURCE_PLUGIN / SOURCE_MCP /
                SOURCE_GENERATED / SOURCE_MARKET）
        source_id: 来源标识符（如插件 ID）

    Returns:
        实际注册的处理函数（名称冲突时可能有别名）
    """
    global _registry_version

    # 处理名称冲突：如果已存在，加数字后缀
    final_name = name
    suffix = 1
    while final_name in _registry:
        suffix += 1
        final_name = f"{name}_{suffix}"

    if final_name != name:
        logger.warning(f"工具 '{name}' 已存在，以 '{final_name}' 注册")
        # 【TASK-04】把"静默改名"变成**可见的别名事实**：名字与来源都记下来，
        # 清单/盘点表据此登记显式 alias（不改变注册行为，D2 向后兼容）。
        _name_conflicts.append({
            "name": name, "final_name": final_name, "kind": "renamed",
            "module": str(getattr(handler, "__module__", "") or ""),
            "source": str(source or ""), "source_id": source_id,
        })

    entry = {
        "name": final_name,
        "description": description,
        "handler": handler,
        "source": source,
        "source_id": source_id,
        "dynamic": source != SOURCE_BUILTIN,
        "registered_at": time.time(),
    }
    if schema:
        entry["schema"] = schema
    _registry[final_name] = entry
    _registry_version += 1
    logger.info(f"动态工具注册: {final_name} (来源: {source}, ID: {source_id})")
    return handler


def set_action_tracker(tracker):
    """设置操作追踪器（可选），用于记录工具调用历史

    Args:
        tracker: ActionTracker 实例，或 None 以清除追踪
    """
    global _action_tracker
    _action_tracker = tracker


def set_discovery_service(service):
    """设置工具发现服务实例

    由 DigitalLife 在初始化时设置，用于 call() 中找不到工具时自动触发发现流程。

    Args:
        service: ToolDiscoveryService 实例，或 None 以清除
    """
    global _discovery_service
    _discovery_service = service


def _update_health(name: str, ok: bool, duration: float):
    """更新工具健康状态"""
    if name not in _tool_health:
        _tool_health[name] = {
            "last_call_time": None,
            "last_ok": True,
            "last_duration": 0.0,
            "call_count": 0,
            "error_count": 0,
        }
    h = _tool_health[name]
    h["last_call_time"] = time.time()
    h["last_ok"] = ok
    h["last_duration"] = duration
    h["call_count"] += 1
    if not ok:
        h["error_count"] += 1


def call(*args, **params) -> Any:
    """调用指定工具

    支持两种调用方式:
      1. call("tool_name", **params) — 标准方式
      2. call(**params_with_name) — params 中包含 name 字段（用于扩展管理工具等场景）

    Args:
        *args: 第一个参数为工具名称（可选，也可从 params 中取）
        **params: 工具参数

    Returns:
        工具执行结果

    Raises:
        ToolError: 工具不存在或执行失败
    """
    # 生成追踪 ID
    trace_id = uuid.uuid4().hex[:12]

    # 从 args 或 params 中提取工具名称，避免 name 关键字冲突
    name = args[0] if args else params.pop("name", None)
    if not name:
        raise ToolError("调用工具时缺少工具名称")

    # ── 注入防御总闸门（TASK-07 机制 2 + 5）──────────────────────────────
    # 【顺序与理由见本文件顶部「注入防御总闸门」一节】放在 `check_tool_call`
    # **之前**：避免为注定不执行的调用挂出悬空审批单；且本层只出 deny、不出 allow，
    # 因此不可能把 `tool_gate` 的既有层短路成死代码（TASK-06 踩过的坑）。
    _injection_denied = check_injection_guard(name, params)
    if _injection_denied is not None:
        return _injection_denied

    # 边界确认凭据是**保留参数**：摘掉后再交给 handler（handler 不该看到它）
    params.pop(BOUNDARY_TOKEN_PARAM, None)

    # 集中式工具闸门（**唯一汇聚点**：所有调用方——含 orchestrator 直连——
    # 都必经此处；fail-open，闸门异常视为放行；被拒直接 return，不抛异常）
    try:
        from agent.tool_gate import check_tool_call as _gate_check
        _denied = _gate_check(name, params)
    except Exception:  # noqa: BLE001  闸门故障/不可用 ⇒ 放行
        _denied = None
    if _denied is not None:
        return _denied

    # 限流检查
    if not _rate_limiter.check(name):
        wait = _rate_limiter.wait_time(name)
        return {"ok": False, "error": f"调用频率过高，请稍后重试", "retry_after": round(wait, 1)}

    tool = _registry.get(name)
    if not tool:
        # 尝试通过发现服务自动获取
        if _discovery_service:
            try:
                logger.info(f"[工具] '{name}' 未找到，尝试自动发现...")
                result = _discovery_service.on_tool_not_found(name, params)
                if result.get("acquired"):
                    logger.info(f"[工具] 自动获取成功: '{name}'")
                    tool = _registry.get(name)
            except Exception as de:
                logger.debug(f"[工具] 自动发现失败: {de}")

        if not tool:
            raise ToolError(f"未知工具: '{name}'，可用工具: {list_tools()}")

    # 操作追踪（可选）
    if _action_tracker:
        target = str(params.get("path", params.get("url", params.get("target", ""))))
        _action_tracker.start_action(name, params, target)

    # 关键工具：额外追踪信息
    if name == "web_search":
        query_preview = str(params.get("query", ""))[:100]
        engine = params.get("engine", "auto")
        logger.info(f"[{trace_id}] 调用工具: {name}, 查询: {query_preview}, 引擎: {engine}")
    elif name == "shell_execute":
        cmd_preview = str(params.get("command", ""))[:100]
        logger.info(f"[{trace_id}] 调用工具: {name}, 命令: {cmd_preview}")
    else:
        logger.info(f"[{trace_id}] 调用工具: {name}, 参数: {params}")

    start = time.time()
    try:
        result = tool["handler"](**params)
        duration = time.time() - start
        _update_health(name, True, duration)

        # 关键工具：记录返回信息
        if name == "web_search":
            if isinstance(result, dict):
                result_count = len(result.get("results", []))
                logger.info(f"[{trace_id}] 工具返回: {name} → 结果数: {result_count}")
            else:
                logger.info(f"[{trace_id}] 工具返回: {name} → {str(result)[:200]}")
        elif name == "shell_execute":
            if isinstance(result, dict):
                exit_code = result.get("returncode", result.get("code", "?"))
                output_size = len(str(result.get("stdout", result.get("output", ""))))
                logger.info(f"[{trace_id}] 工具返回: {name} → 退出码: {exit_code}, 输出大小: {output_size}")
            else:
                logger.info(f"[{trace_id}] 工具返回: {name} → {str(result)[:200]}")
        else:
            logger.info(f"[{trace_id}] 工具返回: {name} → {str(result)[:200]}")

        # 完成操作追踪
        if _action_tracker:
            _action_tracker.finish_action("completed", str(result)[:200])
            if any(k in name for k in ["http", "fetch", "search", "api", "browse"]):
                access_type = "network"
            elif any(k in name for k in ["read", "write", "list", "delete", "rename", "copy"]):
                access_type = "file"
            else:
                access_type = "sensor"
            _action_tracker.log_access(access_type, target or name, name, "allowed")

        # ── 外来文本污点标记（TASK-07 机制 1 的结果侧接线）────────────────
        # 位置：**handler 返回之后、结果交给调用方之前** —— 这是"数据进入上下文
        # 之前"的唯一可靠时点（调用方拿到结果后会立刻拼进消息）。
        # 详见 `agent/guardrails/untrusted_ingest.py`。
        mark_result_foreign(name, result)

        return result
    except Exception as e:
        duration = time.time() - start
        _update_health(name, False, duration)
        logger.error(f"[{trace_id}] 工具执行失败: {name} — {e}")

        # 操作追踪失败
        if _action_tracker:
            _action_tracker.finish_action("failed", str(e)[:200])

        raise ToolError(f"工具 '{name}' 执行失败: {e}") from e


def list_tools() -> list[dict]:
    """列出所有已注册的工具（带缓存）"""
    global _list_tools_cache
    if _list_tools_cache["version"] != _registry_version:
        _list_tools_cache = {
            "version": _registry_version,
            "data": [
                {"name": t["name"], "description": t["description"]}
                for t in _registry.values()
            ],
        }
    return _list_tools_cache["data"]


def list_tools_by_source(source: str) -> list[dict]:
    """按来源列出工具

    Args:
        source: 来源标识（如 SOURCE_PLUGIN）

    Returns:
        匹配的工具列表
    """
    return [
        {"name": t["name"], "description": t["description"]}
        for t in _registry.values()
        if t.get("source") == source
    ]


def unregister_by_source(source: str, source_id: str | None = None) -> int:
    """按来源注销工具组

    用于插件卸载/MCP 断连时批量清理。

    Args:
        source: 来源标识
        source_id: 来源标识符（可选，指定则只注销特定来源实例的工具）

    Returns:
        注销的工具数量
    """
    global _registry_version
    to_remove = [
        name for name, t in _registry.items()
        if t.get("source") == source
        and (source_id is None or t.get("source_id") == source_id)
    ]
    for name in to_remove:
        del _registry[name]
    if to_remove:
        _registry_version += 1
        logger.info(f"按来源注销 {len(to_remove)} 个工具: source={source}, source_id={source_id}")
    return len(to_remove)


def _internal_tool_names() -> frozenset:
    """取"内部工具"名单（`data/tool_definitions/*.yaml` 里 `internal: true` 的工具）

    【不易】任何异常都降级为空集（即不隐藏任何工具）——宁可多暴露，不可因
            YAML 读取失败而把工具静默藏掉，让问题隐形。
    【变易】名单来自 YAML，是数据；随 `_registry_version` 失效重算。
    """
    global _internal_cache
    if _internal_cache["version"] == _registry_version:
        return _internal_cache["data"]
    names: frozenset = frozenset()
    try:
        from agent.lines.models import load_tool_meta
        names = frozenset(n for n, m in load_tool_meta().items() if m.internal)
    except Exception as e:  # noqa: BLE001
        logger.debug("[工具] 内部工具名单加载失败（不隐藏任何工具）: %s", e)
    _internal_cache = {"version": _registry_version, "data": names}
    return names


def _non_callable_names() -> frozenset:
    """取"声明为不可被 LLM 调用"的工具名（`data/tool_definitions/*.yaml` 的可调用性声明）

    【不易】任何异常都降级为空集（即不额外隐藏任何工具）——宁可多暴露，不可因 YAML
            读取失败把工具静默藏掉（与 `_internal_tool_names` 同一纪律）。
    【变易】`CP_TOOL_CALLABILITY_ENFORCE` 取 0/false/no/off ⇒ 退回"只隐藏 internal"。
    """
    global _callability_cache
    if _callability_cache["version"] == _registry_version:
        return _callability_cache["data"]
    names: frozenset = frozenset()
    if str(os.environ.get(_CALLABILITY_ENFORCE_ENV, "1")).strip().lower() \
            not in _CALLABILITY_DISABLED_VALUES:
        try:
            from agent.lines.callability import non_callable_tool_names
            names = non_callable_tool_names()
        except Exception as e:  # noqa: BLE001
            logger.debug("[工具] 可调用性声明加载失败（不额外隐藏工具）: %s", e)
    _callability_cache = {"version": _registry_version, "data": names}
    return names


def _hidden_tool_names() -> frozenset:
    """不给模型看的工具名 = 内部工具 ∪ 声明为不可被 LLM 调用的工具"""
    return _internal_tool_names() | _non_callable_names()


def registry_facts() -> dict[str, dict]:
    """当前注册表里每个工具的**事实**（供可调用性清单与诊断读，不涉及策略）

    返回 `{工具名: {"schema_registered": bool, "host_executor": str,
                    "source": str, "module": str}}`：
      - `schema_registered`：注册时**真的**给了 schema（而非 `get_tool_defs` 兜的
        空壳 `{"type":"object","properties":{}}` —— 那种情况模型不知道参数名）；
      - `host_executor`：`模块:函数` 形态的执行入口；
      - `source`：内置 / 插件 / MCP / 生成 / 市场（见 SOURCE_* 常量）。
    """
    facts: dict[str, dict] = {}
    for name, tool in _registry.items():
        handler = tool.get("handler")
        module = str(getattr(handler, "__module__", "") or "")
        qual = str(getattr(handler, "__qualname__", "") or getattr(handler, "__name__", ""))
        facts[name] = {
            "schema_registered": bool(tool.get("schema")),
            "host_executor": f"{module}:{qual}" if module and qual else "",
            "module": module,
            "source": str(tool.get("source") or SOURCE_BUILTIN),
        }
    return facts


def get_tool_defs(whitelist: list[str] | None = None) -> list[dict]:
    """获取工具定义的 OpenAI/Anthropic 格式列表（带缓存）

    不给模型看的工具**不会**出现在返回结果里（模型看不见），但它们仍在 `_registry`
    中，`call()` 依旧可以按名调用。有两类被隐藏：
      1. `internal: true` 的内部工具（保留注册供后台链路按名调用）；
      2. 声明为不可被 LLM 调用的工具（`llm_callable: false` / `callable_mode: manual`
         / 被权限策略拒绝，见 `agent/lines/callability.py`）。

    Args:
        whitelist: 允许返回的工具名称列表，None 表示全部

    Returns:
        OpenAI-compatible tool definitions list
    """
    hidden = _hidden_tool_names()
    # 无白名单时使用缓存
    if whitelist is None:
        global _get_tool_defs_cache
        if _get_tool_defs_cache["version"] != _registry_version:
            defs = []
            for name, tool in _registry.items():
                if name in hidden:
                    continue
                schema = tool.get("schema", {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                })
                defs.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool["description"],
                        "parameters": schema,
                    }
                })
            _get_tool_defs_cache = {
                "version": _registry_version,
                "data": defs,
            }
        return _get_tool_defs_cache["data"]

    # 有白名单时不使用缓存，实时计算
    defs = []
    for name, tool in _registry.items():
        if whitelist and name not in whitelist:
            continue
        if name in hidden:
            continue
        schema = tool.get("schema", {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        })
        defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": schema,
            }
        })
    return defs


def get_tool_schema(name: str) -> dict | None:
    """获取指定工具的 JSON Schema

    Args:
        name: 工具名称

    Returns:
        工具参数的 JSON Schema，如果工具不存在则返回 None
    """
    tool = _registry.get(name)
    if not tool:
        return None
    return tool.get("schema", {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    })


def sync_web_search_engines(engine_names: list[str], search_engine=None) -> bool:
    """同步 web_search 工具的可用搜索引擎列表（动态更新 enum）

    当搜索引擎新增/删除时调用，让 LLM 知道哪些引擎可以用。

    Args:
        engine_names: 可用的搜索引擎名称列表
        search_engine: 可选的 SearchEngine 实例，传入后自动过滤不可用的引擎

    Returns:
        bool: 是否成功更新
    """
    tool = _registry.get("web_search")
    if not tool:
        logger.warning("[工具] web_search 工具未注册，无法同步引擎列表")
        return False

    # 如果传入了 SearchEngine 实例，过滤出真正可用的引擎
    if search_engine is not None:
        try:
            available = search_engine.get_available_engines()
            engine_names = [
                e["name"] for e in available
                if e.get("enabled", True)
                and (not e.get("needs_key") or e.get("configured"))
            ]
        except Exception:
            pass  # 回退到传入的 engine_names

    if not engine_names:
        engine_names = []  # 至少为空列表

    schema = tool.get("schema", {})
    props = schema.get("properties", {})
    engine_prop = props.get("engine")
    if engine_prop is None:
        logger.warning("[工具] web_search 工具的 engine 参数不存在")
        return False

    engine_prop["enum"] = engine_names
    engine_prop["description"] = (
        f"搜索引擎名称（可选）。可用引擎: {', '.join(engine_names)}。"
        "不指定则按优先级自动选择"
    )
    global _registry_version
    _registry_version += 1
    logger.info(f"[工具] web_search 引擎 enum 已同步: {engine_names}")
    return True


def get_health_status() -> dict:
    """获取所有工具的健康状态

    Returns:
        dict: {
            "tools": {tool_name: {last_call_time, last_ok, last_duration, call_count, error_count}},
            "overall_score": int (0-100),
            "total_tools": int,
            "healthy_tools": int,
        }
    """
    tools_health = dict(_tool_health)
    total = len(tools_health)

    if total == 0:
        return {
            "tools": {},
            "overall_score": 100,
            "total_tools": 0,
            "healthy_tools": 0,
        }

    healthy_count = sum(1 for h in tools_health.values() if h["last_ok"])
    # 计算整体评分: 基于健康工具比例和错误率
    total_calls = sum(h["call_count"] for h in tools_health.values())
    total_errors = sum(h["error_count"] for h in tools_health.values())

    if total_calls == 0:
        # 尚未有任何调用，返回完美评分
        overall_score = 100
    else:
        error_rate = total_errors / total_calls
        healthy_ratio = healthy_count / total
        # 加权评分: 健康比例占 70%，错误率占 30%
        overall_score = int(healthy_ratio * 70 + (1 - error_rate) * 30)
        overall_score = max(0, min(100, overall_score))

    return {
        "tools": {
            name: {
                "last_call_time": h["last_call_time"],
                "last_ok": h["last_ok"],
                "last_duration": round(h["last_duration"], 4),
                "call_count": h["call_count"],
                "error_count": h["error_count"],
            }
            for name, h in tools_health.items()
        },
        "overall_score": overall_score,
        "total_tools": total,
        "healthy_tools": healthy_count,
    }


def clear():
    """清空工具注册表（主要用于测试）"""
    global _registry_version, _list_tools_cache, _get_tool_defs_cache, _callability_cache
    _registry.clear()
    _registry_version += 1
    _list_tools_cache = {"version": -1, "data": None}
    _get_tool_defs_cache = {"version": -1, "data": None}
    _callability_cache = {"version": -1, "data": frozenset()}
    _tool_health.clear()


# ════════════════════════════════════════════════════════════
#  动态工具持久化入口（薄转发到 agent.tools.persistence）
# ════════════════════════════════════════════════════════════
# 【为什么转发而不实现在这里】
#   lifecycle_manager.py:1005-1012 一直调用本模块的
#   init_dynamic_tools_persistence / load_dynamic_tools，但这两个名字此前**不存在**，
#   于是每次启动都 AttributeError → 被 except 吞成 warning ⇒ 自生成的工具重启即失。
#   实现放在独立模块（persistence.py）便于单独测试与替换，这里只做转发保持调用口径。

def init_dynamic_tools_persistence(index_path: str | None = None) -> str:
    """初始化动态工具持久化（见 agent.tools.persistence 模块文档）"""
    from agent.tools.persistence import init_dynamic_tools_persistence as _f
    return _f(index_path)


def load_dynamic_tools() -> int:
    """加载全部持久化的动态工具，返回成功加载数"""
    from agent.tools.persistence import load_dynamic_tools as _f
    return _f()


def persist_dynamic_tool(name: str, description: str = "",
                         schema: dict | None = None, **kwargs) -> bool:
    """登记动态工具并补写治理声明 YAML（见 persistence.ensure_yaml_definition）"""
    from agent.tools.persistence import persist_dynamic_tool as _f
    return _f(name, description, schema, **kwargs)
