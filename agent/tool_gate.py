"""集中式工具闸门（fail-open + 显式拒绝）——工具分发主链路上的唯一收口点

背景（为什么需要它）：
    强制点选在 **``agent/tools/__init__.py::call()`` 的最前面**——它是工具分发的
    **唯一汇聚点**：``agent/tool_calling.py::_execute_safe`` → ``_execute_safe_core``
    → ``tools.call(...)``，以及编排主流程里的**直连**调用（``agent/orchestrator/
    orchestrator.py:3519`` / ``:3557`` 的 ``_tools.call(_fn_name, **_fn_args)``）最终
    都落到这里。只把闸门挂在 ``_execute_safe`` 上会漏掉那两条直连路径（等于装在半扇
    门上），因此必须收口在 ``call()``。
    而在本闸门之前，这条必经之路上只有限流、熔断与 trace 记录，**没有任何集中的
    权限/审批闸门**；权限判定靠"每个工具自己记得调用
    ``dl._permission.check_action(...)``"——可选、易漏，新工具漏写一次就等于该工具
    无权限。本模块给这条必经路径补上一个集中、可关、失败即放行的闸门。

为什么不复用 ``PermissionGateway`` 的 RBAC 白名单（关键事实，勿删）：
    ``agent/permission_system.py:611 PermissionGateway.check()`` 的第一层 RBAC 是
    **严格白名单**语义：``_check_rbac``（L823-867）在 ``tool_name`` 既不在该角色的
    ``allowed_tools`` 里、也不匹配 ``"*"`` 时返回 ``allowed=False``；角色在策略文件
    里**不存在**时同样直接拒绝（L832-838）。
    而 ``data/permission_policies.json`` 的 ``default_role`` 是 **``guest``**，其
    ``allowed_tools`` 只有 ``["web_search", "read_file"]`` ⇒ 严格白名单下 74 个真实
    工具里**只有 2 个能过**（封杀 72 个 ≈ 97.3%）。
    （历史核对：修复前策略里的工具名与真实注册表**严重错位**——``file_read`` /
    ``file_write`` / ``code_runner`` / ``system_format`` / ``system_shutdown`` 在注册表里
    **并不存在**，真实名字是 ``read_file`` / ``write_file`` / ``code_review``，当时是 72 个
    工具里只放行 1 个 ≈ 98.6%。错位已修，但那只是把"几乎全封"改善成"绝大多数被封"。）
    ⇒ 把 RBAC 白名单直接套到这条**必经**路径上，``default_role=guest`` 仍会**封杀
    绝大多数工具**——"接入权限"变成"系统瘫痪"。这不是保守与否的取舍。
    因此本闸门的**默认**语义被刻意定为 **fail-open + 显式拒绝**：只有策略文件或描述符
    **显式**声明拒绝/需要审批时才拦，其余一律放行；RBAC 白名单留在它原有的、由各工具
    自行调用 ``check_action`` 的位置上，**不在此处收口**。
    （现状核对：``data/permission_policies.json`` 各角色 ``denied_tools`` 里没有真实
    注册工具名，``data/descriptors.json`` 描述符的 ``trust.requires_approval`` 全为
    false ⇒ 本闸门接入后对既有行为**零影响**；日后由数据侧显式收紧。）

判定顺序（除显式拒绝外一律放行）：
    0. 总开关 ``CP_TOOL_GATE_ENABLED`` 取值为 ``0/false/no/off``（大小写不敏感）
       → 直接放行（缺省视为开启）；
    1. 读 ``data/permission_policies.json``，取**所有角色** ``roles[*].denied_tools``
       的**并集**：含 ``"*"`` → 拒绝一切工具；含本次工具名 → 拒绝；
    2. 读 ``data/descriptors.json``，该工具的描述符 ``trust.requires_approval=true``
       → 拒绝（按工具原名与 canonical id ``cp.<source>.<name>`` 双向查找，任一命中
       即算命中）；
    3. **治理平面审批边界（``data/tool_definitions/*.yaml``，唯一真相）**：该工具的
       元数据 ``needs_approval`` 为真 ⇒ **默认只记 warning 不拦截**；仅当
       ``CP_TOOL_GATE_APPROVAL_ENFORCE`` 取 ``1/true/yes/on`` 时才返回结构化拒绝
       （``error_code="APPROVAL_REQUIRED"``）。详见下方"治理平面审批边界"一节；
    4. 其余情况 → 放行（返回 ``None``）；
    5. **（可选，默认关闭）严格模式** —— 仅当 ``CP_TOOL_GATE_STRICT`` 取
       ``1/true/yes/on`` 时才执行，且**只在第 1–3 步都未拒绝之后**追加一道
       ``PermissionGateway.check()`` 的 RBAC + ABAC 判定；网关返回
       ``allowed=False`` → 拒绝。详见下方"严格模式"一节。

治理平面审批边界（``CP_TOOL_GATE_APPROVAL_ENFORCE``，**默认关闭**）：
    背景：``data/tool_definitions/*.yaml`` 是"哪个工具多危险"的唯一真相，其
    ``ToolMeta.needs_approval``（``plane == "govern"`` 或 ``effect == "extend"`` 或
    ``risk == "critical"``）在 ``agent/lines/models.py`` 里被声明为审批边界，但**改动前
    没有任何代码真的按它拦过**——实测 ``generate_tool`` / ``ext_install`` /
    ``connect_mcp`` / ``shell_execute`` / ``write_file`` / ``remember`` 全部返回
    ``None``（放行），``denied_tools`` 并集为空、``requires_approval`` 索引为 0、
    严格模式关闭 ⇒ **97 个工具零拦截**，治理框架齐全却一条规则都没生效。
    本节把"治理平面 = 审批边界"接进必经路径，并**用开关控制它是否真的拦截**：
        开关：``CP_TOOL_GATE_APPROVAL_ENFORCE`` ∈ ``1/true/yes/on``（大小写不敏感、
            两侧空白忽略）⇒ 命中即拒绝（``{"ok": False, "blocked": True,
            "error_code": "APPROVAL_REQUIRED", "error": ..., "tool": ..., "reason": ...}``）；
            **未设置或其它任何取值 ⇒ 只 ``logger.warning`` 记一条、照常放行**（默认口径
            里"零行为变化"这条不变量优先，且审批 UI 尚未接入 ⇒ 默认拦截等于把所有治理
            类工具变成"永远失败"，那是"接入审批"变成"系统瘫痪"的另一种写法）。
    为什么**不**复用 ``CP_TOOL_GATE_STRICT``：严格模式的语义是 **RBAC 白名单**，与"这个
        工具要不要人工审批"是两件事（``owner`` 的 ``allowed_tools=["*"]`` 下
        ``shell_execute`` 会通过 RBAC 却仍属审批边界）。更重要的是，严格模式的既有契约
        是"开启后 ``owner`` 下常用工具**放行**"（``tests/unit/test_tool_gate_strict.py``
        的 ``test_严格模式加_owner_下_shell_execute_不被拒`` 钉死了这一点）——把审批边界
        挂到同一个变量上会当场打破那条契约。故**新增独立开关**，两者可自由组合。
    为什么读不到元数据时**不** fail-closed（与 ``HITLManager.assess`` 刻意不对称）：
        本闸门的既定纪律是 fail-open（"闸门出错绝不断工具执行"，见下方"健壮性纪律"），
        未登记工具只记 ``warning``。真正的 fail-closed 在审批权威那里——
        ``agent/human_in_the_loop/hitl.py::HITLManager.assess`` 对未登记工具返回 HIGH。
        两处不对称是**刻意**的：安全判据从严，闸门自身从宽。
    回滚方式：去掉 ``CP_TOOL_GATE_APPROVAL_ENFORCE``（或置 ``0``）⇒ 回到"只告警"口径。


严格模式（``CP_TOOL_GATE_STRICT``，**默认关闭**）：
    开关：``CP_TOOL_GATE_STRICT`` ∈ ``1/true/yes/on``（大小写不敏感、两侧空白忽略）
    才启用；**未设置或其它任何取值一律保持上面的 fail-open 行为**。
    启用后追加的判定：
        角色 = 环境变量 ``CP_PERMISSION_DEFAULT_ROLE``（缺省 ``owner``；取值不是
        ``Role`` 枚举合法值 → 告警并回退 ``owner``）；
        来源 = 环境变量 ``CP_PERMISSION_SESSION_SOURCE``（缺省 ``"cli"``）；
        ``PermissionGateway.check(tool_name, params, ABACContext(role=..., session_source=...))``
        返回 ``PermissionResult(allowed=False)`` ⇒ 拒绝（拒绝结构与既有规则一致，
        文案注明"RBAC 严格模式所拒"并带上网关的 ``reason``）。
    为什么默认关闭（这是本节的唯一重点）：
        ① ``default_role`` 是 ``guest``，其严格白名单只放行 74 个工具中的 2 个
           （封杀 ≈ 97.3%）——直接开启等于"当场把系统打瘫"；
        ② ABAC 的 ``time_outside`` 会让某个工具**在一天中的某些时段失效** ⇒ 造成
           **时段相关的偶发失败**：同一份代码白天全绿、夜里变红，且与代码改动无关，
           是最难排查的一类失败。
           **现状**：原先唯一的时段规则 ``off-hours-shell-restriction``
           （``shell_execute`` + ``time_outside ["09:00","18:00"]``）已从
           ``data/permission_policies.json`` 的 ``abac_rules`` **移除**（意图与恢复方法
           留档在同文件的 ``_policy_notes``）——因为本机所有者是 **18:00 开始工作**，
           该窗口会把他的整个工作时段禁掉。所以**当前数据下** ② 不会发生；
           但**机制仍在**：任何人把 ``time_outside`` 规则加回 ``abac_rules``（数据侧），
           或将来新增同类规则，② 就会立刻回来。故这一条仍未过时。
           （配套修复：``PermissionGateway._time_in_window`` 原先用字符串比较
           ``start <= now <= end``，``start > end`` 时**恒为假** ⇒ 连"18:00–06:00
           这种跨午夜作息"都表达不出来；现已支持跨午夜窗口，改窗口即可按自己的作息配置。）
    开启前必须先确认（缺一不可）：
        1. ``data/permission_policies.json`` 的 ``default_role`` **不是** ``guest``，
           或 ``CP_PERMISSION_DEFAULT_ROLE`` 指向一个白名单符合预期的角色
           （本仓库为此新增了 ``owner``：``allowed_tools=["*"]``、``denied_tools=[]``，
           ABAC 仍生效——它正是"开了不会当场全封"的那个角色）；
        2. 该角色在策略文件的 ``roles`` 里**真实存在**（角色不存在 ⇒ 直接全量拒绝）；
        3. 清楚 ``session_source`` 该报什么（缺省 ``"cli"``；报 ``"scheduled"`` 会命中
           ``scheduled-no-write`` / ``scheduled-no-edit`` 两条 ABAC 规则）。
    回滚方式：**去掉 ``CP_TOOL_GATE_STRICT`` 环境变量**（或置为 ``0``）即可回到
        fail-open 口径；本层不改任何数据文件、不改任何默认值，故回滚无残留。

``session_source`` 的取值优先级（高 → 低）：
    ① ``check_tool_call(..., session_source="scheduled")`` **调用方显式传入**；
    ② 上下文变量 ``current_session_source()``——由 ``set_session_source("scheduled")``
       设置，返回的句柄可用于 ``with`` 语句或 ``.reset()``；**默认空**（空 ⇒ 落到 ③）；
    ③ 环境变量 ``CP_PERMISSION_SESSION_SOURCE``；
    ④ 缺省 ``"cli"``（＝改动前的唯一口径）。
    为什么要有 ②（这是本机制存在的唯一理由，别删）：环境变量是**进程级**的，无法区分
    "这一次调用来自定时任务"还是"来自人机对话"——把 ``CP_PERMISSION_SESSION_SOURCE``
    整体设成 ``scheduled`` 会让**所有**会话都报定时任务来源，等于把两条 ABAC 规则变成
    全局禁令。``contextvars`` 是**按执行上下文**的，于是同一进程里两条链路可以报出不同
    的来源：``agent/task_scheduler.py::run_task`` 在**执行线程内**设置 ``scheduled``，
    人机对话链路不设置（读到空 ⇒ 落到环境变量/缺省 ``cli``）。
    注意：``contextvars`` **不跨线程继承**——在调度线程里设置对执行线程无效，新线程读到
    的永远是空值。所以设置点必须在真正执行任务的那个线程内部（``run_task`` 体内，
    而非 ``_run_loop``/``tick`` 内）。
    本节只新增①/②两级，**不新增任何环境变量**（环境变量须在
    ``agent/settings/registry.py`` 登记），③/④ 一字未改 ⇒ 不设任何环境变量时行为与改动前一致。

健壮性纪律（本闸门自身的 bug **绝不能**阻断工具执行）：
    - 文件缺失 / JSON 解析失败 / 结构异常 / 任何未预期异常 → 一律放行 + ``logger.warning``；
    - **严格层同样如此**：网关构造失败、策略文件降级、``PermissionResult`` 结构异常
      （缺 ``allowed`` / ``reason`` 字段）等任何异常 → 放行 + 告警，绝不让闸门自身的
      bug 阻断工具执行。**但网关明确返回 ``allowed=False`` 时必须真的拒绝**——
      fail-open 针对的是"闸门出错"，不是"网关说不行"。
    - 导入期不做文件 IO 与重活（严格模式的网关**惰性构造并缓存**，仿
      ``agent/task_scheduler.py`` 的回退权限对象写法）；每次调用读文件，按
      ``(st_mtime_ns, st_size)`` 轻量缓存；
    - 纯标准库（json/logging/os/re/threading/typing）；不调用 shell；**不写任何数据文件**。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, FrozenSet, Iterator, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 模块级常量（测试可 monkeypatch 两个路径常量指向 tmp_path 下的假文件）
# ─────────────────────────────────────────────────────────────

#: 仓库根（本文件位于 <root>/agent/tool_gate.py）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: RBAC 角色策略文件——本闸门**只读** ``roles[*].denied_tools`` 的并集，不读白名单
POLICY_POLICIES_PATH = os.path.join(_REPO_ROOT, "data", "permission_policies.json")

#: 工具描述符台账——本闸门**只读** ``trust.requires_approval``
DESCRIPTORS_PATH = os.path.join(_REPO_ROOT, "data", "descriptors.json")

#: 总开关环境变量（缺省开启；见 ``_disabled_by_env``）
GATE_ENABLED_ENV = "CP_TOOL_GATE_ENABLED"

#: 视为"关闭"的取值（大小写不敏感）
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})

# ── RBAC 严格模式（**默认关闭**；见模块 docstring"严格模式"一节）──────────────
#: 严格模式开关（未设置/其它值 ⇒ 保持既有 fail-open 行为）
STRICT_ENABLED_ENV = "CP_TOOL_GATE_STRICT"
#: 严格模式使用的角色（缺省 owner）
STRICT_ROLE_ENV = "CP_PERMISSION_DEFAULT_ROLE"
#: 严格模式使用的会话来源（缺省 cli）
STRICT_SOURCE_ENV = "CP_PERMISSION_SESSION_SOURCE"
#: 视为"启用"的取值（大小写不敏感、两侧空白忽略）
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
#: 严格模式缺省角色：`owner` 的白名单是 ``["*"]``，开了不会当场封杀 97.3% 的工具
_DEFAULT_STRICT_ROLE = "owner"
#: 严格模式缺省会话来源（报 "scheduled" 会命中 scheduled-no-write / scheduled-no-edit）
_DEFAULT_STRICT_SOURCE = "cli"

#: ``denied_tools`` 通配符：命中即拒绝一切工具
_WILDCARD = "*"

#: 拒绝结果的 error_code（结构对齐项目既有工具失败约定：``ok=False``）
ERROR_CODE_PERMISSION_DENIED = "PERMISSION_DENIED"

# ── 治理平面审批边界（YAML 派生；见模块 docstring 同名一节）──────────────────
#: 显式审批边界开关（**默认关闭**；未设置/其它值 ⇒ 只告警不拦截）
APPROVAL_ENFORCE_ENV = "CP_TOOL_GATE_APPROVAL_ENFORCE"
#: 审批边界拒绝的 error_code（与 PERMISSION_DENIED 区分：这是"要先审批"，不是"不许用"）
ERROR_CODE_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"

#: ``data/tool_definitions/*.yaml`` 元数据缓存（``None`` = 尚未加载；按需加载）
_TOOL_META_CACHE: Any = None
_META_LOCK = threading.Lock()

#: canonical capability_id 的默认来源段（与 agent/descriptors/bridge.py 内置工具构式同构）
_DEFAULT_SOURCE_ID = "builtin"

#: 缓存路径数上限（超出即整体清空，保证缓存不无界增长）
_CACHE_MAX = 64

_CACHE_LOCK = threading.Lock()
#: path → ((st_mtime_ns, st_size), 派生结果)
_DERIVED_CACHE: Dict[str, Tuple[Tuple[int, int], Any]] = {}
#: 已告警过的 key（同一异常不刷屏）
_WARNED: Set[str] = set()

#: 严格模式的 PermissionGateway 单例（**惰性构造**：导入期零重活）
_STRICT_GATEWAY: Any = None
_GATEWAY_LOCK = threading.Lock()

#: id 段清洗（与 agent/descriptors/bridge.py::sanitize_id_part 同构，保持 join 键一致）
_ID_PART_RE = re.compile(r"[^0-9A-Za-z_.\-]+")
_ID_DASH_RE = re.compile(r"-{2,}")


# ─────────────────────────────────────────────────────────────
# 会话来源的调用上下文（contextvar；**默认空** ⇒ 落到环境变量/缺省 cli）
# ─────────────────────────────────────────────────────────────
# Why contextvar 而不是环境变量：环境变量是**进程级**的，无法区分"这一次调用来自定时
#   任务"还是"来自人机对话"（把 CP_PERMISSION_SESSION_SOURCE 整体设成 scheduled 等于把
#   两条 ABAC 规则变成全局禁令）。contextvars 是按执行上下文的，于是同一进程里两条链路
#   可以报出不同的来源。
# Why 默认空串而不是 "cli"：空 ⇒ 走既有的环境变量/缺省链路（第 ③/④ 级），语义上
#   "本次上下文没有声明来源"，与"声明了 cli"是两件事。缺省 "cli" 会让"未声明"与
#   "显式声明 cli"无法区分，也就无法保留改动前的取值优先级。
# 线程语义（**关键**）：contextvars **不跨线程继承**——在调度线程里设置对执行线程无效，
#   新线程读到的永远是空值。故设置点必须在真正执行任务的那个线程内部；反过来，这也正是
#   "定时任务报 scheduled、人机对话报 cli"能互不串味的原因。
_SESSION_SOURCE_VAR: contextvars.ContextVar = contextvars.ContextVar(
    "cp_permission_session_source", default="",
)


class _SessionSourceHandle:
    """``set_session_source()`` 的返回值：可用于 ``with``，也可手动 ``reset()``

    语义与 ``contextvars.Token`` 一致（且只允许 reset 一次），只是额外支持 ``with``：:

        with set_session_source("scheduled"):
            ...            # 本上下文内 current_session_source() == "scheduled"
        # 退出即还原（含异常路径）

    也可用 ``try/finally``：``handle = set_session_source(...)`` / ``handle.reset()``。
    """

    __slots__ = ("_token",)

    def __init__(self, token: Any) -> None:
        self._token = token

    @property
    def token(self) -> Any:
        """底层 ``contextvars.Token``（需要手工 ``ContextVar.reset`` 时用）"""
        return self._token

    def reset(self) -> None:
        """还原到设置前的值；重复调用是幂等的空操作"""
        token = self._token
        if token is None:
            return
        self._token = None
        _SESSION_SOURCE_VAR.reset(token)

    def __enter__(self) -> "_SessionSourceHandle":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.reset()
        return False


def set_session_source(source: str) -> _SessionSourceHandle:
    """把**当前执行上下文**的会话来源设为 ``source``（返回可 ``with``/``reset`` 的句柄）

    只影响当前上下文（当前线程 / 当前 asyncio 任务），**不改环境变量、不写任何文件**；
    句柄 ``reset()``（或 ``with`` 退出）即还原。空串/空白 ⇒ 等价于"本次上下文未声明来源"
    （判定时落到环境变量 ``CP_PERMISSION_SESSION_SOURCE``、再落到缺省 ``"cli"``）。

    **设置点必须在真正执行任务的线程内部**——contextvars 不跨线程继承，在调度线程里
    设置对执行线程无效（``agent/task_scheduler.py::run_task`` 即按此在体内设置）。
    """
    return _SessionSourceHandle(_SESSION_SOURCE_VAR.set(str(source or "").strip()))


def current_session_source() -> str:
    """读当前执行上下文的会话来源；**未设置/空白 ⇒ 空串**（调用方据此走下一级优先级）

    不抛异常（极端情况下 contextvar 不可用也返回空串——闸门自身的健壮性纪律）。
    """
    try:
        raw = _SESSION_SOURCE_VAR.get()
    except Exception:  # noqa: BLE001  上下文不可读 ⇒ 按"未声明来源"处理
        return ""
    return str(raw or "").strip()


def reset_session_source(handle: Any) -> None:
    """还原会话来源：接受 :func:`set_session_source` 的句柄，也接受裸 ``Token``

    ``None`` / 非法对象 ⇒ 静默空操作（便于 ``try/finally`` 里无脑调用）。
    """
    if handle is None:
        return
    try:
        reset = getattr(handle, "reset", None)
        if callable(reset):
            reset()
            return
        _SESSION_SOURCE_VAR.reset(handle)
    except Exception as e:  # noqa: BLE001  还原失败不得影响主流程（只告警）
        logger.warning("[tool_gate] 会话来源还原失败: %s: %s", type(e).__name__, e)


# ─────────────────────────────────────────────────────────────
# 对外入口
# ─────────────────────────────────────────────────────────────


def check_tool_call(func_name: str, args: Optional[Dict[str, Any]] = None,
                    dl: Any = None,
                    session_source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """集中式工具闸门：返回 ``None`` = 放行；返回 dict = 拒绝结果（直接作为工具结果返回）

    判定顺序（模块 docstring 有完整口径；**除显式拒绝外一律放行**）：

    1. 总开关 ``CP_TOOL_GATE_ENABLED`` 为 0/false/no/off → 放行；
    2. ``roles[*].denied_tools`` 并集含 ``"*"`` → 拒绝一切；含本次工具名 → 拒绝；
    3. 描述符 ``trust.requires_approval=true``（工具原名或 canonical id 命中）→ 拒绝；
    4. **治理平面审批边界**：``data/tool_definitions/*.yaml`` 的 ``needs_approval``
       为真 ⇒ **默认只记 warning 不拦截**；``CP_TOOL_GATE_APPROVAL_ENFORCE`` 取
       1/true/yes/on 时 → 拒绝（``error_code="APPROVAL_REQUIRED"``）；
    5. **（可选，默认关闭）** ``CP_TOOL_GATE_STRICT`` 取 1/true/yes/on 时，追加
       ``PermissionGateway.check()`` 的 RBAC+ABAC 判定；``allowed=False`` → 拒绝；
    6. 其余 → 放行。

    **为什么默认不用 RBAC 白名单**：见模块 docstring——``default_role`` 是严格白名单
    语义的 ``guest``（只放行 74 个工具中的 2 个），直接套用会封杀 ≈97.3% 的工具。
    这是本闸门默认 fail-open 的硬理由；严格模式只作为**显式开启**的选项存在。

    健壮性：本函数**不抛异常**。文件缺失/JSON 损坏/结构异常/任何未预期异常一律
    放行并记 ``logger.warning``；闸门自身的 bug 绝不能阻断工具执行。**严格层亦然**
    （含网关构造失败与策略降级），但网关明确返回 ``allowed=False`` 时**会真的拒绝**。

    Args:
        func_name: 工具名（也接受 canonical id ``cp.<source>.<name>`` 形态）
        args: 本次调用的参数。默认口径下**不参与判定**（保留扩展位：参数级规则）；
              严格模式开启时会作为 ``params`` 传给 ``PermissionGateway.check()``
        dl: DigitalLife 实例（可选）。**当前不参与判定**（保留扩展位：会话/角色上下文）
        session_source: **（可选，默认 None）** 本次调用的会话来源，仅严格模式使用。
            取值优先级：本参数 → ``current_session_source()``（由 ``set_session_source``
            设置的上下文变量）→ 环境变量 ``CP_PERMISSION_SESSION_SOURCE`` → 缺省
            ``"cli"``。传 ``None``/空串等于"本次未声明"（向后兼容：既有调用方不传该参数，
            行为与改动前完全一致）。

    Returns:
        ``None`` = 放行；否则为
        ``{"ok": False, "blocked": True, "error_code": "PERMISSION_DENIED", "error": <原因>}``
    """
    try:
        if _disabled_by_env():
            return None

        name = str(func_name or "").strip()
        if not name:
            return None

        denied = _cached_derived(POLICY_POLICIES_PATH, _build_denied_union)
        if _WILDCARD in denied:
            return _deny(name, "权限策略把所有工具列入黑名单（来源: %s 的 "
                              "roles[*].denied_tools 含通配符 %r）"
                         % (POLICY_POLICIES_PATH, _WILDCARD))
        hit = _blacklist_hit(name, denied)
        if hit is not None:
            return _deny(name, "该工具在权限策略的显式黑名单中（来源: %s 的 "
                              "roles[*].denied_tools，命中条目 %r）"
                         % (POLICY_POLICIES_PATH, hit))

        # 2. 描述符审批边界（data/descriptors.json 的 trust.requires_approval）
        #
        # 【2026-09-17 行为统一】这一步原先是**硬拒绝**，其正确性依赖模块 docstring
        #   里写明的一条前提：「descriptors.json 的 trust.requires_approval **全为
        #   false** ⇒ 本闸门接入后对既有行为零影响」。
        #   而 `scripts/backfill_tool_descriptors.py` 把描述符从 3/91 补到 91/91 后，
        #   该前提失效：`shell_execute`（critical）首次变成 requires_approval=true，
        #   于是这一步被激活，**默认配置下直接拒掉 shell 执行**——而第 3 步（YAML 的
        #   同一语义）是刻意做成"默认只告警、开关才拦"的。
        #   同一语义两种行为，是缺陷而非特性：会让"补齐元数据"这种纯数据修正
        #   意外变成"关掉一项能力"。
        #   现统一为：与第 3 步共用 APPROVAL_ENFORCE_ENV 开关，默认**只告警不拦截**。
        #   要真正启用审批边界：设 CP_TOOL_GATE_APPROVAL_ENFORCE=1（一处开关管两个来源）。
        index = _cached_derived(DESCRIPTORS_PATH, _build_approval_index)
        cid = _approval_hit(name, index)
        if cid is not None:
            reason = ("描述符要求人工审批（%s 能力 %s 的 trust.requires_approval=true）"
                      % (DESCRIPTORS_PATH, cid))
            if _approval_enforce_enabled():
                return _deny(name, "该工具的%s；请先走审批流程后再调用" % reason)
            _warn_once(
                "approval_desc:" + name,
                "工具 %s 的%s，但 %s 未开启 ⇒ 本次仅告警、不拦截（设 %s=1 即启用审批边界）",
                name, reason, APPROVAL_ENFORCE_ENV, APPROVAL_ENFORCE_ENV,
            )

        # 3. 治理平面审批边界（**唯一真相：data/tool_definitions/*.yaml**）。
        #    默认只告警不拦截（见模块 docstring）；显式开启后返回结构化拒绝。
        approval_reason = _approval_boundary(name)
        if approval_reason is not None:
            if _approval_enforce_enabled():
                return _deny_approval(name, approval_reason)
            _warn_once(
                "approval:" + name,
                "工具 %s 按 data/tool_definitions/*.yaml 的元数据需要人工审批（%s），"
                "但 %s 未开启 ⇒ 本次仅告警、不拦截（设 %s=1 即启用审批边界）",
                name, approval_reason, APPROVAL_ENFORCE_ENV, APPROVAL_ENFORCE_ENV,
            )

        # 4. 【可选，默认关闭】RBAC 严格模式：只在上面几步都未拒绝之后才追加。
        #    本步自带 fail-open 边界（见 _strict_deny）：严格层内部的任何异常都放行，
        #    但网关**明确返回 allowed=False** 时必须真的拒绝。
        if _strict_enabled():
            strict_denied = _strict_deny_or_open(name, args, session_source)
            if strict_denied is not None:
                return strict_denied
        return None
    except Exception as e:  # noqa: BLE001  闸门自身故障绝不断工具执行（fail-open）
        logger.warning("[tool_gate] 闸门判定异常（按 fail-open 放行）: %s: %s",
                       type(e).__name__, e)
        return None


# ─────────────────────────────────────────────────────────────
# 拒绝结果构造
# ─────────────────────────────────────────────────────────────


def _deny(func_name: str, reason: str) -> Dict[str, Any]:
    """构造拒绝结果

    结构对齐项目既有工具失败约定 ``{"ok": False, "error": ...}``；``blocked: True``
    沿用既有先例（``agent/tools/file_tools_reg.py`` 的 write_file 拒绝分支）。
    """
    message = "工具 %s 被集中式工具闸门拒绝: %s" % (func_name, reason)
    logger.warning("[tool_gate] %s", message)
    return {
        "ok": False,
        "blocked": True,
        "error_code": ERROR_CODE_PERMISSION_DENIED,
        "error": message,
    }


def _deny_approval(func_name: str, reason: str) -> Dict[str, Any]:
    """构造"需要人工审批"的拒绝结果（治理平面审批边界专用）

    与 :func:`_deny` 的区别只在 ``error_code`` 与额外两个结构化字段——
    ``PERMISSION_DENIED`` 是"这个工具不许用"，``APPROVAL_REQUIRED`` 是"这个工具要先
    走审批"。调用方（如审批 UI）据此区分"硬拒绝"与"待审批"，故**不能**复用前者。
    """
    message = ("工具 %s 被集中式工具闸门拒绝: 该工具按 data/tool_definitions/*.yaml 的"
               "元数据需要人工审批（%s）；当前 %s 已开启，请先走审批流程后再调用"
               % (func_name, reason, APPROVAL_ENFORCE_ENV))
    logger.warning("[tool_gate] %s", message)
    return {
        "ok": False,
        "blocked": True,
        "error_code": ERROR_CODE_APPROVAL_REQUIRED,
        "error": message,
        "tool": func_name,
        "reason": reason,
    }


# ─────────────────────────────────────────────────────────────
# 治理平面审批边界（``data/tool_definitions/*.yaml`` 的 needs_approval）
# ─────────────────────────────────────────────────────────────


def _tool_meta() -> Dict[str, Any]:
    """惰性加载并缓存 ``data/tool_definitions/*.yaml`` 的能力元数据

    【为什么缓存】``check_tool_call()`` 在**每次工具调用**上执行，而加载要读 97 个
        YAML ⇒ 不缓存等于把 97 次文件 IO 放进热路径。
    【简易】加载失败（缺 ``yaml`` 依赖 / 目录不可读 / 任何异常）⇒ 空 dict，**不抛异常**：
        闸门自身的故障绝不断工具执行（fail-open 纪律）；只记一条 ``_warn_once`` 告警
        （同一原因不刷屏），随后按"无审批规则可依"放行。
    """
    global _TOOL_META_CACHE
    if _TOOL_META_CACHE is None:
        with _META_LOCK:
            if _TOOL_META_CACHE is None:
                try:
                    from agent.lines import load_tool_meta
                    _TOOL_META_CACHE = dict(load_tool_meta())
                except Exception as e:  # noqa: BLE001  读不到元数据 ⇒ 无审批规则可依
                    _warn_once("meta-load", "工具元数据加载失败（审批边界失效，"
                                            "按无规则放行）: %s: %s", type(e).__name__, e)
                    _TOOL_META_CACHE = {}
    return _TOOL_META_CACHE


def _approval_enforce_enabled() -> bool:
    """审批边界开关：``CP_TOOL_GATE_APPROVAL_ENFORCE`` ∈ {1,true,yes,on} 才真的拦截

    **未设置或其它任何取值一律返回 False**（＝回到"只记 warning、照常放行"的默认口径）。
    读取异常按"未启用"处理——审批边界只在被显式要求时才收紧。
    """
    raw = _env_str(APPROVAL_ENFORCE_ENV)
    if raw is None:
        return False
    return raw.lower() in _ENABLED_VALUES


def _approval_boundary(func_name: str) -> Optional[str]:
    """该工具是否落在"治理平面 = 审批边界"上；是则返回人类可读原因，否则 ``None``

    判据来自 ``data/tool_definitions/<tool>.yaml`` 经 ``agent.lines.load_tool_meta()``
    读出的 ``ToolMeta.needs_approval``：
        ``plane == "govern"`` 或 ``effect == "extend"`` 或 ``risk == "critical"``。

    Args:
        func_name: 工具名（也接受 canonical id ``cp.<source>.<name>`` 形态）

    Returns:
        需要审批时返回形如 ``"plane=govern, effect=extend, risk=critical"`` 的原因串；
        **不需要审批、或查不到该工具的元数据**时返回 ``None``。

    【为什么查不到元数据时返回 None 而不是 fail-closed】本闸门是 fail-open 闸门
        （"闸门自身的 bug 绝不能阻断工具执行"），未登记工具只告警不拦；安全侧的
        fail-closed 由审批权威 ``agent/human_in_the_loop/hitl.py::HITLManager.assess``
        承担（未登记工具 ⇒ HIGH）。这个不对称是刻意的。
    """
    metas = _tool_meta()
    if not metas:
        return None
    raw = str(func_name or "").strip()
    if not raw:
        return None
    candidates = [raw, raw.lower(), _last_segment(raw)]
    meta = None
    for key in candidates:
        if key and key in metas:
            meta = metas[key]
            break
    if meta is None:
        return None
    if not bool(getattr(meta, "needs_approval", False)):
        return None
    return ("plane=%s, effect=%s, risk=%s"
            % (getattr(meta, "plane", "?"), getattr(meta, "effect", "?"),
               getattr(meta, "risk", "?")))


# ─────────────────────────────────────────────────────────────
# 总开关
# ─────────────────────────────────────────────────────────────


def _disabled_by_env() -> bool:
    """总开关：``CP_TOOL_GATE_ENABLED`` ∈ {0,false,no,off}（大小写不敏感）→ 整体放行

    缺省（未设置/空串以外无法识别）视为**开启**。读取异常按"未关闭"处理（放行由
    调用方的 fail-open 兜底，此处只需不抛异常）。
    """
    try:
        raw = os.environ.get(GATE_ENABLED_ENV)
    except Exception:  # noqa: BLE001  环境变量不可读 → 不改变缺省语义
        return False
    if raw is None:
        return False
    return str(raw).strip().lower() in _DISABLED_VALUES


# ─────────────────────────────────────────────────────────────
# RBAC 严格模式（可选；**默认关闭**——见模块 docstring"严格模式"一节）
# ─────────────────────────────────────────────────────────────


def _env_str(name: str) -> Optional[str]:
    """读环境变量并去空白；读不到（未设置/空串/读取异常）→ ``None``

    环境变量不可读时返回 ``None``（＝走缺省值），不抛异常。
    """
    try:
        raw = os.environ.get(name)
    except Exception:  # noqa: BLE001  环境变量不可读 → 按未设置处理
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _strict_enabled() -> bool:
    """严格模式开关：``CP_TOOL_GATE_STRICT`` ∈ {1,true,yes,on}（大小写不敏感）才启用

    **未设置或其它任何取值一律返回 False**（＝保持既有 fail-open 行为）。
    读取异常按"未启用"处理——严格模式只在被显式要求时才可能收紧。
    """
    raw = _env_str(STRICT_ENABLED_ENV)
    if raw is None:
        return False
    return raw.lower() in _ENABLED_VALUES


def _strict_role() -> Any:
    """严格模式使用的角色（``CP_PERMISSION_DEFAULT_ROLE``；缺省/非法 → ``owner``）

    返回 ``agent.permission_system.Role`` 枚举成员。只接受合法枚举值；**非法值告警并
    回退 ``_DEFAULT_STRICT_ROLE``**（不抛异常——回退比"因为拼错一个环境变量就变成
    全量拒绝"安全得多）。``Role`` 导入失败向上抛，由 :func:`_strict_deny_or_open`
    的 fail-open 边界放行。
    """
    from agent.permission_system import Role

    raw = _env_str(STRICT_ROLE_ENV)
    if raw is None:
        return Role(_DEFAULT_STRICT_ROLE)
    try:
        return Role(raw.lower())
    except Exception as e:  # noqa: BLE001  非法角色值 → 回退缺省角色并告警
        _warn_once("strict-role:" + raw,
                   "严格模式角色取值非法（回退 %s）: %r (%s)",
                   _DEFAULT_STRICT_ROLE, raw, type(e).__name__)
        return Role(_DEFAULT_STRICT_ROLE)


def _strict_source(explicit: Optional[str] = None) -> str:
    """严格模式使用的会话来源；取值优先级（高 → 低）：

    ① ``explicit``——``check_tool_call(..., session_source=...)`` 显式传入；
    ② ``current_session_source()``——上下文变量（``set_session_source`` 设置；默认空）；
    ③ 环境变量 ``CP_PERMISSION_SESSION_SOURCE``；
    ④ 缺省 ``"cli"``（＝改动前的唯一口径，不设任何环境变量时结果一字不变）。

    空白/``None`` 一律视为"本级别未声明"，继续往下一级找 ⇒ 上下文变量置空即回到
    环境变量/缺省链路（这正是"还原正确性"赖以成立的语义）。
    """
    for candidate in (explicit, current_session_source(), _env_str(STRICT_SOURCE_ENV)):
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return text
    return _DEFAULT_STRICT_SOURCE


def _strict_gateway() -> Any:
    """惰性构造并缓存 ``PermissionGateway``（导入期零重活）

    Why 惰性：本模块被 ``agent/tools/__init__.py`` 在**每次工具调用**时导入，导入期
    做策略文件 IO / 构造三层的网关属于"在热路径上做重活"。写法仿
    ``agent/task_scheduler.py:67-84`` 的回退权限对象（模块级惰性单例）。

    Why 显式绝对策略路径：``PermissionGateway.DEFAULT_POLICY_PATH`` 是**相对路径**
    ``"data/permission_policies.json"``，按**当前工作目录**解析；从非仓库根启动时
    会加载失败 ⇒ ``_degraded=True`` ⇒ 跳过 RBAC/ABAC（严格模式会静默退化成"只走正则"）。
    这里显式传仓库内绝对路径，让"严格"是真的严格。

    构造失败**向上抛**，由 :func:`_strict_deny_or_open` 的 fail-open 边界兜底。
    """
    global _STRICT_GATEWAY
    if _STRICT_GATEWAY is None:
        with _GATEWAY_LOCK:
            if _STRICT_GATEWAY is None:
                from agent.permission_system import PermissionGateway
                _STRICT_GATEWAY = PermissionGateway(
                    policy_path=os.path.join(_REPO_ROOT, "data",
                                             "permission_policies.json"),
                )
    return _STRICT_GATEWAY


def _strict_deny_or_open(func_name: str, args: Optional[Dict[str, Any]],
                         session_source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """执行严格层判定，**fail-open 边界就在这个函数里**

    Returns:
        ``None`` ＝ 放行（含"网关出错/策略降级/未知结构"等 fail-open 情形）；
        ``dict`` ＝ 拒绝（只在网关**明确**返回 ``allowed=False`` 时产生）。

    **fail-open 边界（写死在这里，别挪走）**：
        网关构造失败、策略文件降级、``check()`` 抛异常、返回值缺 ``allowed`` 字段等
        **任何**异常 ⇒ ``logger.warning`` + 放行。闸门自身的 bug 绝不阻断工具执行。
        但网关**明确**给出 ``allowed=False`` 时必须真的拒绝——fail-open 针对的是
        "闸门出错"，不是"网关说不行"。
    """
    try:
        from agent.permission_system import ABACContext

        role = _strict_role()
        source = _strict_source(session_source)
        gateway = _strict_gateway()
        context = ABACContext(role=role, session_source=source)
        result = gateway.check(func_name, dict(args or {}), context)
        if result is None or bool(getattr(result, "allowed", True)):
            return None
        reason = str(getattr(result, "reason", "") or "未提供原因")
        requires_confirmation = bool(getattr(result, "requires_confirmation", False))
        return _deny(
            func_name,
            "RBAC 严格模式（%s=%s）所拒：角色 %r 不允许调用该工具"
            "（会话来源 %r；网关 reason: %s%s）"
            % (STRICT_ENABLED_ENV, _env_str(STRICT_ENABLED_ENV),
               getattr(role, "value", role), source, reason,
               "；网关标记为需要二次确认" if requires_confirmation else ""),
        )
    except Exception as e:  # noqa: BLE001  严格层自身故障 ⇒ **放行**（见上方边界说明）
        logger.warning("[tool_gate] 严格模式判定异常（按 fail-open 放行）: %s: %s",
                       type(e).__name__, e)
        return None


# ─────────────────────────────────────────────────────────────
# 轻量缓存（按 (mtime_ns, size) 失效）
# ─────────────────────────────────────────────────────────────


def _reset_cache() -> None:
    """清空派生缓存与告警去重表（仅供测试在改写临时文件后调用）

    一并清掉工具元数据缓存：测试若替换了 ``data/tool_definitions/``（或想验证
    "读不到元数据"的分支），必须让下一次判读取到新值。
    """
    global _TOOL_META_CACHE
    with _CACHE_LOCK:
        _DERIVED_CACHE.clear()
        _WARNED.clear()
    with _META_LOCK:
        _TOOL_META_CACHE = None


def _warn_once(key: str, message: str, *args: Any) -> None:
    """同一 key 只告警一次（闸门异常不该在每次工具调用时刷屏）"""
    with _CACHE_LOCK:
        if key in _WARNED:
            return
        if len(_WARNED) >= 256:
            _WARNED.clear()
        _WARNED.add(key)
    logger.warning("[tool_gate] " + message, *args)


def _stamp(path: str) -> Optional[Tuple[int, int]]:
    """文件指纹 ``(st_mtime_ns, st_size)``；不可读 → None"""
    try:
        st = os.stat(path)
    except Exception:  # noqa: BLE001  文件不可读 ⇒ 无规则可依（放行）
        return None
    try:
        return (int(st.st_mtime_ns), int(st.st_size))
    except Exception:  # noqa: BLE001  极老平台的 stat 结构差异 → 放弃缓存
        return None


def _read_json(path: str) -> Any:
    """读 JSON；解析/IO 失败返回 ``None``（放行），**不抛异常**"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001  JSON 损坏/权限/编码异常 → fail-open
        _warn_once("parse:" + path, "策略/台账文件解析失败（按 fail-open 放行）: %s: %s",
                   path, e)
        return None


def _cached_derived(path: str, builder: Callable[[Any], Any]) -> Any:
    """按文件指纹缓存 ``builder(_read_json(path))`` 的结果

    缓存失效：指纹变化即重建；文件不存在时不做正缓存（下次仍重新探测，文件出现即生效）。
    """
    stamp = _stamp(path)
    if stamp is None:
        _warn_once("missing:" + path, "策略/台账文件不存在（按 fail-open 放行）: %s", path)
        with _CACHE_LOCK:
            _DERIVED_CACHE.pop(path, None)
        return builder(None)

    with _CACHE_LOCK:
        cached = _DERIVED_CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]

    value = builder(_read_json(path))
    with _CACHE_LOCK:
        if len(_DERIVED_CACHE) >= _CACHE_MAX:
            _DERIVED_CACHE.clear()
        _DERIVED_CACHE[path] = (stamp, value)
    return value


# ─────────────────────────────────────────────────────────────
# 规则 1：denied_tools 并集
# ─────────────────────────────────────────────────────────────


def _build_denied_union(data: Any) -> FrozenSet[str]:
    """``roles[*].denied_tools`` 的并集；结构异常 ⇒ 空集（＝不拒绝任何工具）

    注意：**只取黑名单**。``allowed_tools`` 是白名单，本闸门刻意不读（见模块 docstring
    "为什么不复用 RBAC 白名单"）。
    """
    if data is None:
        return frozenset()
    if not isinstance(data, dict):
        _warn_once("policy-shape", "权限策略顶层不是对象（按无规则放行）: %s",
                   type(data).__name__)
        return frozenset()

    roles = data.get("roles")
    if roles is None:
        return frozenset()
    if not isinstance(roles, dict):
        _warn_once("policy-roles-shape", "权限策略 roles 不是对象（按无规则放行）: %s",
                   type(roles).__name__)
        return frozenset()

    denied: Set[str] = set()
    for role_name, cfg in roles.items():
        if not isinstance(cfg, dict):
            _warn_once("policy-role:" + str(role_name), "角色 %s 结构异常（跳过该角色）",
                       role_name)
            continue
        tools = cfg.get("denied_tools")
        if tools is None:
            continue
        if not isinstance(tools, (list, tuple, set, frozenset)):
            _warn_once("policy-denied:" + str(role_name),
                       "角色 %s 的 denied_tools 不是列表（跳过该角色）", role_name)
            continue
        for item in tools:
            if isinstance(item, str) and item.strip():
                denied.add(item.strip())
    return frozenset(denied)


def _blacklist_hit(func_name: str, denied: FrozenSet[str]) -> Optional[str]:
    """并集中与本次工具名等价的条目（原名 ↔ canonical id 两种写法互为等价）"""
    if not denied:
        return None
    keys = _query_keys(func_name)
    for entry in denied:
        stripped = entry.strip()
        if stripped.lower() in keys:
            return stripped
    return None


# ─────────────────────────────────────────────────────────────
# 规则 2：描述符 requires_approval
# ─────────────────────────────────────────────────────────────


def _iter_descriptor_entries(data: Any) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """台账 → ``(capability_id, entry)`` 迭代；结构异常 ⇒ 空迭代（fail-open）

    兼容三种形态：``{"descriptors": {id: entry}}``（仓库现状）、``{id: entry}``、
    ``[entry, ...]``（id 取自 ``meta.id``）。其余结构只告警不拦。
    """
    if data is None:
        return
    if isinstance(data, dict):
        container: Any = data.get("descriptors", data)
    elif isinstance(data, list):
        container = data
    else:
        _warn_once("desc-shape", "描述符台账结构无法识别（按无规则放行）: %s",
                   type(data).__name__)
        return

    if isinstance(container, dict):
        for cid, entry in container.items():
            if isinstance(entry, dict):
                yield str(cid), entry
    elif isinstance(container, list):
        for entry in container:
            if not isinstance(entry, dict):
                continue
            meta = entry.get("meta")
            cid = meta.get("id") if isinstance(meta, dict) else ""
            yield str(cid or ""), entry
    else:
        _warn_once("desc-container", "描述符台账容器结构无法识别（按无规则放行）: %s",
                   type(container).__name__)


def _build_approval_index(data: Any) -> Dict[str, str]:
    """``requires_approval=true`` 的描述符 → ``{查找键(小写): capability_id}``

    只收审批要求为真的条目 ⇒ 台账里其余（现状：全部）描述符不会影响判定。
    """
    index: Dict[str, str] = {}
    for cid, entry in _iter_descriptor_entries(data):
        trust = entry.get("trust")
        if not isinstance(trust, dict):
            continue
        if not _truthy(trust.get("requires_approval")):
            continue
        for key in _descriptor_keys(cid, entry):
            index.setdefault(key, cid or key)
    return index


def _approval_hit(func_name: str, index: Dict[str, str]) -> Optional[str]:
    """本次工具名命中的（要求审批的）capability_id；未命中 → None"""
    if not index:
        return None
    for key in _query_keys(func_name):
        cid = index.get(key)
        if cid:
            return cid
    return None


# ─────────────────────────────────────────────────────────────
# 名字/ID 归一（工具原名 ↔ canonical id 双向可查）
# ─────────────────────────────────────────────────────────────


def _sanitize_id_part(value: str) -> str:
    """id 段清洗：小写，非 [a-z0-9_.-] → "-"，折叠并去首尾分隔符（与 bridge 同构）"""
    s = _ID_PART_RE.sub("-", str(value or "").strip().lower())
    s = _ID_DASH_RE.sub("-", s)
    return s.strip("._-")[:120]


def _canonical_candidate(func_name: str) -> str:
    """工具名 → canonical capability_id 候选（已是 ``cp.*`` 则原样返回）"""
    raw = str(func_name or "").strip()
    if not raw:
        return ""
    if raw.startswith("cp."):
        return raw
    sanitized = _sanitize_id_part(raw)
    return "cp.%s.%s" % (_DEFAULT_SOURCE_ID, sanitized) if sanitized else ""


def _last_segment(name: str) -> str:
    """取 id/名字的最后一段（``cp.builtin.read_file`` → ``read_file``）；无点返回空串"""
    raw = str(name or "").strip()
    if "." not in raw:
        return ""
    return raw.rsplit(".", 1)[1].strip().lower()


def _query_keys(func_name: str) -> Set[str]:
    """一次调用的全部查找键：原名 / 原名清洗形 / canonical id / id 名字段"""
    raw = str(func_name or "").strip()
    if not raw:
        return set()
    keys = {raw.lower()}
    sanitized = _sanitize_id_part(raw)
    if sanitized:
        keys.add(sanitized)
    canonical = _canonical_candidate(raw)
    if canonical:
        keys.add(canonical.lower())
    segment = _last_segment(raw)
    if segment:
        keys.add(segment)
    keys.discard("")
    return keys


def _descriptor_keys(cid: str, entry: Dict[str, Any]) -> Set[str]:
    """描述符的查找键：capability_id / meta.id / 名字段 / ``capability.name`` 及其清洗形"""
    keys: Set[str] = set()
    for candidate in (cid, _last_segment(cid)):
        if candidate:
            keys.add(str(candidate).lower())
    meta = entry.get("meta")
    if isinstance(meta, dict):
        meta_id = meta.get("id")
        if isinstance(meta_id, str) and meta_id.strip():
            keys.add(meta_id.strip().lower())
    capability = entry.get("capability")
    name = capability.get("name") if isinstance(capability, dict) else None
    if isinstance(name, str) and name.strip():
        keys.add(name.strip().lower())
        sanitized_name = _sanitize_id_part(name)
        if sanitized_name:
            keys.add(sanitized_name)
    keys.discard("")
    return keys


def _truthy(value: Any) -> bool:
    """宽松判定 JSON 布尔：True/"true"/1 视为真；其余为假（不认识就不拦——fail-open）"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return False
