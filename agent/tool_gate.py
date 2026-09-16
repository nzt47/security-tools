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
    3. 其余情况 → 放行（返回 ``None``）；
    4. **（可选，默认关闭）严格模式** —— 仅当 ``CP_TOOL_GATE_STRICT`` 取
       ``1/true/yes/on`` 时才执行，且**只在第 1–2 步都未拒绝之后**追加一道
       ``PermissionGateway.check()`` 的 RBAC + ABAC 判定；网关返回
       ``allowed=False`` → 拒绝。详见下方"严格模式"一节。

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
# 对外入口
# ─────────────────────────────────────────────────────────────


def check_tool_call(func_name: str, args: Optional[Dict[str, Any]] = None,
                    dl: Any = None) -> Optional[Dict[str, Any]]:
    """集中式工具闸门：返回 ``None`` = 放行；返回 dict = 拒绝结果（直接作为工具结果返回）

    判定顺序（模块 docstring 有完整口径；**除显式拒绝外一律放行**）：

    1. 总开关 ``CP_TOOL_GATE_ENABLED`` 为 0/false/no/off → 放行；
    2. ``roles[*].denied_tools`` 并集含 ``"*"`` → 拒绝一切；含本次工具名 → 拒绝；
    3. 描述符 ``trust.requires_approval=true``（工具原名或 canonical id 命中）→ 拒绝；
    4. **（可选，默认关闭）** ``CP_TOOL_GATE_STRICT`` 取 1/true/yes/on 时，追加
       ``PermissionGateway.check()`` 的 RBAC+ABAC 判定；``allowed=False`` → 拒绝；
    5. 其余 → 放行。

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

        index = _cached_derived(DESCRIPTORS_PATH, _build_approval_index)
        cid = _approval_hit(name, index)
        if cid is not None:
            return _deny(name, "该工具的描述符要求人工审批（trust.requires_approval=true，"
                              "来源: %s 能力 %s）；请先走审批流程后再调用"
                         % (DESCRIPTORS_PATH, cid))

        # 4. 【可选，默认关闭】RBAC 严格模式：只在上面两步都未拒绝之后才追加。
        #    本步自带 fail-open 边界（见 _strict_deny）：严格层内部的任何异常都放行，
        #    但网关**明确返回 allowed=False** 时必须真的拒绝。
        if _strict_enabled():
            strict_denied = _strict_deny_or_open(name, args)
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


def _strict_source() -> str:
    """严格模式使用的会话来源（``CP_PERMISSION_SESSION_SOURCE``；缺省 ``cli``）"""
    raw = _env_str(STRICT_SOURCE_ENV)
    return raw if raw is not None else _DEFAULT_STRICT_SOURCE


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


def _strict_deny_or_open(func_name: str, args: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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
        source = _strict_source()
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
    """清空派生缓存与告警去重表（仅供测试在改写临时文件后调用）"""
    with _CACHE_LOCK:
        _DERIVED_CACHE.clear()
        _WARNED.clear()


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
