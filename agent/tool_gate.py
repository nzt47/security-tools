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
    （现状核对【2026-09-22 更正】：``data/permission_policies.json`` 各角色
    ``denied_tools`` 里仍没有真实注册工具名；但 ``data/descriptors.json`` 已有 **10 条**
    描述符 ``trust.requires_approval=true``（``shell_execute`` / ``run_sandbox`` /
    ``generate_tool`` / ``ext_*`` / ``*_mcp`` 等，见
    ``scripts/backfill_tool_descriptors.py`` 的批量回填）。此处原写作"全为 false ⇒
    零影响"，那句话在回填之后**已失效**，留着会误导排查。
    这 10 条与 YAML 的 ``needs_approval`` **逐条一致**（都是 ``risk: critical`` 或
    ``plane=govern/effect=extend`` ⇒ 派生 L3），即两个来源不冲突；
    但它们走的是**先于分级层**的同一条 ``_tool_approval_outcome``，
    故"描述符侧命中"时不会经过分级层的身份/非交互判定与分级开关。）

判定顺序（除显式拒绝外一律放行）：
    0. 总开关 ``CP_TOOL_GATE_ENABLED`` 取值为 ``0/false/no/off``（大小写不敏感）
       → 直接放行（缺省视为开启）；
    1. 读 ``data/permission_policies.json``，取**所有角色** ``roles[*].denied_tools``
       的**并集**：含 ``"*"`` → 拒绝一切工具；含本次工具名 → 拒绝；
    2. 读 ``data/descriptors.json``，该工具的描述符 ``trust.requires_approval=true``
       → 拒绝（按工具原名与 canonical id ``cp.<source>.<name>`` 双向查找，任一命中
       即算命中）；
    3. **治理平面审批边界（``data/tool_definitions/*.yaml``，唯一真相）**：该工具的
       元数据 ``needs_approval`` 为真 ⇒ **默认拦截**：先查是否已有人工批准（有则消费后
       放行，单次有效）、是否已被人工驳回（驳回则明确拒绝、不许重试）；否则**幂等挂单**
       并返回 ``{"error_code": "APPROVAL_REQUIRED", "approval_id": ...}``，等人工在
       审批收件箱裁决后**原样重试**即放行。置 ``CP_TOOL_GATE_APPROVAL_ENFORCE=0``
       可退回"只记 warning 不拦截"。详见下方"治理平面审批边界"一节；
    4. 其余情况 → 放行（返回 ``None``）；
    5. **（可选，默认关闭）严格模式** —— 仅当 ``CP_TOOL_GATE_STRICT`` 取
       ``1/true/yes/on`` 时才执行，且**只在第 1–4 步都未拒绝之后**追加一道
       ``PermissionGateway.check()`` 的 RBAC + ABAC 判定；网关返回
       ``allowed=False`` → 拒绝。详见下方"严格模式"一节。

治理平面审批边界（``CP_TOOL_GATE_APPROVAL_ENFORCE``，**默认开启**）：
    背景：``data/tool_definitions/*.yaml`` 是"哪个工具多危险"的唯一真相，其
    ``ToolMeta.needs_approval``（``plane == "govern"`` 或 ``effect == "extend"`` 或
    ``risk == "critical"``）在 ``agent/lines/models.py`` 里被声明为审批边界，但**改动前
    没有任何代码真的按它拦过**——实测 ``generate_tool`` / ``ext_install`` /
    ``connect_mcp`` / ``shell_execute`` / ``write_file`` / ``remember`` 全部返回
    ``None``（放行），``denied_tools`` 并集为空、``requires_approval`` 索引为 0、
    严格模式关闭 ⇒ **91 个工具零拦截**，治理框架齐全却一条规则都没生效。
    本节把"治理平面 = 审批边界"接进必经路径，并接上**能走通的审批闭环**：
        - 命中审批边界 ⇒ 经 ``agent/tool_approval.py`` 向既有审批流（``ApprovalFlow``，
            收件箱 UI 同源）**挂单**，返回 ``APPROVAL_REQUIRED`` + ``approval_id`` +
            ``guidance``（告诉模型：人工确认后原样重试）；
        - 人工在审批控制台（``GET /approval-console``，或 CLI ``scripts/approve_tool_call.ps1``）
            批准后，**同一次调用**（同一工具 + 同一参数摘要 + 同一会话）再进来 ⇒ 消费该批准
            （**单次有效**）并放行；
            同一会话）再进来 ⇒ 消费该批准（**单次有效**）并放行；
        - 人工驳回 ⇒ 返回 ``APPROVAL_REJECTED`` 并带上否决理由，模型不应重试；
        - 重启后仍然有效：批准记录落在 ``data/approval_records.jsonl``，消费台账落在
            ``data/tool_approval_uses.jsonl``（审批流是决策权威，台账只解决"不可重放"）。
    默认值为何反转（2026-09-17）：原先是"未设置＝不拦截"，理由是"审批 UI 尚未接到工具
        调用上 ⇒ 默认拦截等于让治理类工具永远失败"。闭环接通后该理由消失——拦截现在是
        一条**能走通**的路径。回滚只需 ``CP_TOOL_GATE_APPROVAL_ENFORCE=0``。
    为什么**不**复用 ``CP_TOOL_GATE_STRICT``：严格模式的语义是 **RBAC 白名单**，与"这个
        工具要不要人工审批"是两件事（``owner`` 的 ``allowed_tools=["*"]`` 下
        ``shell_execute`` 会通过 RBAC 却仍属审批边界）。故**独立开关**，两者可自由组合。
    为什么读不到元数据时**不** fail-closed（与 ``HITLManager.assess`` 刻意不对称）：
        本闸门的既定纪律是 fail-open（"闸门出错绝不断工具执行"，见下方"健壮性纪律"），
        未登记工具只记 ``warning``。真正的 fail-closed 在审批权威那里——
        ``agent/human_in_the_loop/hitl.py::HITLManager.assess`` 对未登记工具返回 HIGH。
        两处不对称是**刻意**的：安全判据从严，闸门自身从宽。
        **但审批边界这一层是例外**：一旦确认该工具属于审批边界，而桥接层不可用／挂单失败，
        本层**不放行**（返回"需要审批"并附失败原因）——"证不出已批准"就不能执行。
    回滚方式：``CP_TOOL_GATE_APPROVAL_ENFORCE=0`` ⇒ 回到"只告警、照常放行"口径。


严格模式（``CP_TOOL_GATE_STRICT``，**默认关闭**）：
    开关：``CP_TOOL_GATE_STRICT`` ∈ ``1/true/yes/on``（大小写不敏感、两侧空白忽略）
    才启用；**未设置或其它任何取值一律保持上面的 fail-open 行为**。
    启用后追加的判定：
        角色 = 环境变量 ``CP_PERMISSION_DEFAULT_ROLE``（缺省 ``guest``——**最小权限**；
        取值不是 ``Role`` 枚举合法值 → 告警并回退同一个 ``guest``）；
        【2026-09-20 TASK-06 E15 改动】缺省值原为 ``owner``（``allowed_tools=["*"]``
        ⇒ 严格模式等于没开）。TASK-06 §3 第 6 步第 6 项要求"缺省角色改为**最小权限**
        （拒绝或只读）"，理由是"缺省特权"这条设计一旦被别人依赖，
        就会让"开了严格模式"这句话失去意义。要宽松口径请显式设
        ``CP_PERMISSION_DEFAULT_ROLE=owner``（既有开关，无需新变量）。
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
#: 严格模式缺省角色
#
# 【🔴 TASK-06（E15）改动：`owner` → `guest`】
#   原值 `owner` 的理由是"`owner` 的白名单是 `["*"]`，开了不会当场封杀 97.3% 的工具"
#   —— 那是一个**为了不破坏功能而选的缺省**，代价是"严格模式一旦开启，缺省即特权"：
#   `owner` = `allowed_tools: ["*"]` = 放行一切，于是"开了严格模式"与"什么都没开"
#   在缺省配置下**行为完全相同**（严格模式的 RBAC 层形同虚设）。
#   治理面的缺省必须是**最小权限**而不是特权（本模块 docstring 的 fail-closed 纪律）。
#   实测（`data/permission_policies.json`）：`guest.allowed_tools` 只有 **2** 条，
#   是四个角色里最少的一个 ⇒ 缺省降到最小权限。
#
#   【影响评估（E16）】本改动**当前零行为影响**：`CP_TOOL_GATE_STRICT` 在 `.env` 与
#   `config.yaml` 里实测**都不存在**（见 TASK-06 §2.2）⇒ 严格模式整段判定从不执行。
#   它的作用是在**将来有人打开严格模式时**，缺省落在安全侧而不是特权侧。
#   想恢复旧行为：显式设 `CP_PERMISSION_DEFAULT_ROLE=owner`（既有开关，无需新变量）。
_DEFAULT_STRICT_ROLE = "guest"
#: 严格模式缺省会话来源（报 "scheduled" 会命中 scheduled-no-write / scheduled-no-edit）
_DEFAULT_STRICT_SOURCE = "cli"

#: ``denied_tools`` 通配符：命中即拒绝一切工具
_WILDCARD = "*"

#: 拒绝结果的 error_code（结构对齐项目既有工具失败约定：``ok=False``）
ERROR_CODE_PERMISSION_DENIED = "PERMISSION_DENIED"

# ── 治理平面审批边界（YAML 派生；见模块 docstring 同名一节）──────────────────
#: 审批控制台的**人工入口**（页面壳，不含数据；数据端点仍走 /api/approval/* 的令牌链）。
#: 为什么把具体入口写进拦截回执：原回执只说"请人工在「治理 → 审批收件箱」确认"——
#: 那句话本身没错（React 工作台确有该侧栏项：`workbench/hubNav.tsx` 的
#: 治理面板 → 审批收件箱），但它**没有告诉人不在这里时怎么办**。实测（2026-09-22）
#: 这条独立控制台页在浏览器里**打不开**：`/api/approval/console` 挂 `@require_token`，
#: 而地址栏导航带不了 Authorization 头（且它自己的 JS 当时也不注入令牌）⇒ 403/401。
#: 故回执改为点名**两处可直接照做**的入口（本页与 CLI），并说清"参数要逐字相同"。
#: 端口是 app_server.py 的固定绑定；换端口部署时改这一行即可（只是一句文案）。
APPROVAL_CONSOLE_URL = "http://127.0.0.1:5678/approval-console"

# ── 治理平面审批边界（YAML 派生；见模块 docstring 同名一节）──────────────────
#: 审批边界开关（**默认开启**；显式置 0/false/no/off 才退回"只告警不拦截"）
APPROVAL_ENFORCE_ENV = "CP_TOOL_GATE_APPROVAL_ENFORCE"
#: 审批边界拒绝的 error_code（与 PERMISSION_DENIED 区分：这是"要先审批"，不是"不许用"）
ERROR_CODE_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
#: 人工已**驳回**该次调用的 error_code（与"待审批"区分：不要再重试，重试也没用）
ERROR_CODE_APPROVAL_REJECTED = "APPROVAL_REJECTED"

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
# 工具侧确认分级 L0–L3（v1.4 §10.2 / TASK-06 §3 第 2 步）
# ─────────────────────────────────────────────────────────────
#
# 【四级的可判定语义（本模块是**唯一执行点**）】
#
# | 级别 | 人（交互） | SA（有 scope 预授权） | 非交互且无 SA 预授权 | 身份不可知 |
# |---|---|---|---|---|
# | L0 | 免确认 | 放行 | 放行 | 放行 |
# | L1 | 摘要确认（**会话内可复用**，可批量） | 放行（scope 覆盖即可） | **拒绝，且不挂单** | 拒绝 |
# | L2 | 逐次确认（**单次有效**） | 放行（scope 须覆盖该能力） | **拒绝，且不挂单** | 拒绝 |
# | L3 | 逐次确认（单次有效） | 放行（scope 必须显式允许 **L3**） | **拒绝，且不挂单** | 拒绝 |
#
# 【不易·L3 为什么不是"人也不能执行"】TASK-06 §3 第 2 步第 3 项把 L3 写作
#   "禁止：默认拒绝，只有显式预授权（SA + scope）才能执行"。若按字面实现成
#   "人类点批准也不行"，则 §1 的**完成判据**（"risk: critical 的工具在**人**调用时
#   得到**逐次确认**"）自相矛盾，且会一次性打挂既有审批闭环对 10 个
#   govern/extend/critical 工具的支持（`tests/unit/test_tool_gate.py` 与
#   `test_tool_approval_e2e.py` 覆盖的正是这条链路）。
#   ⇒ 故 **L3 的"默认禁止"落在"禁止非交互 / 无身份的自动执行"这一侧**，
#     人对 L3 仍走逐次确认。这与 §1 完成判据逐字一致。
#
# 【L1 的"可批量确认"如何落地】L1 的批准**不消费**（会话内 + TTL 内可复用），
#   L2/L3 的批准**消费**（单次有效，复用既有 `tool_approval_uses.jsonl` 台账）。
#   这正是 `tool_gate.py:769-773` 当初把 high 降级不拦的动机（"否则所有写操作都要
#   点确认"）—— 现在用**分级**满足它，而不是一刀切。

#: 确认分级强制开关（**默认开启**；置 0 ⇒ 退回"只有旧的 needs_approval 集合挂单"）
CONFIRM_LEVEL_ENFORCE_ENV = "CP_TOOL_CONFIRM_LEVEL_ENFORCE"

#: 豁免名单**消费侧**的变更留痕动作名（A2/R2 护栏）
#:
#: 【为什么消费侧也要记一条】名单有两个观察者，覆盖的**通路不同**：
#:   · 写入侧 `settings.change`（subject=`setting:<KEY>`）—— 只覆盖「经开关中心/UI 改」
#:     的通路，payload 里有 old/new/applied（实测链上 9 条，见 A2.md §S1）；
#:   · 消费侧（本动作）—— 在闸门**真正要执行这份名单**的那一刻对拍
#:     「链上已知值 vs 当前生效值」，覆盖**不经过开关中心**的通路：直接改
#:     `data/ui_settings.json`、改 `.env`/`config.yaml` 后重启、进程环境变量被注入。
#:   两条记录**不重复**：消费侧只在「当前值 ≠ 链上已知值」时写（见
#:   :func:`_record_exempt_change_if_needed`），而链上已知值本身已包含写入侧那条。
EXEMPT_CHANGE_ACTION = "tool.confirm.exempt_changed"

#: 写入侧动作名 / 主题前缀（**只做字符串比对**，不 import settings 包 —— 避免依赖边）
_SETTINGS_CHANGE_ACTION = "settings.change"
_SETTINGS_SUBJECT_PREFIX = "setting:"

#: 确认分级的**操作员豁免名单**（逗号分隔的工具名 / 能力 id；**默认空 = 一个都不豁免**）
#:
#: 【为什么放宽只能走这里，而不是写进 YAML】
#:   有些能力是**持续高频的编排动作**（最典型的是 `delegate`：每派一个子代理都要
#:   人去收件箱点一次摘要确认），逐次确认的收益极低、摩擦极高 —— 实测体感就是
#:   "子代理不可用"，而子代理**自己发出的工具调用仍会各自过闸门**（`agent/tools/__init__.py::call`
#:   是唯一汇聚点）⇒ 放宽的只是"委派这个动作本身"，不是子代理能干什么。
#:   而仓库**不允许**用 YAML 里的 `confirm_level` 来放宽：
#:   `tests/unit/test_confirm_level.py::Test为什么YAML零声明` 已裁定那是同一事实的
#:   第二份口径（D1 禁止），并且"调 risk 却忘了调 confirm_level"会变成新的漂移源。
#:   ⇒ 放宽必须由**操作员显式声明的运行期开关**承担，与既有两个开关同款：
#:   默认关、可一处回滚、逐次留痕。
#:
#: 【它**不**放宽什么（这条比上面的理由更重要）】只跳过分级层里的"摘要确认"：
#:   · 权限策略 `denied_tools` 黑名单、描述符 `trust.requires_approval`、
#:     受限会话 `sandbox_allowed` ⇒ 都判在本层**之前**，照旧生效；
#:   · **伦理硬规则**与"未登记工具的 HITL 兜底" ⇒ 在 `check_tool_call` 第 4 步
#:     **重新**判定一次，不因本开关失效（由 `TestConfirmLevelExemption` 钉住）；
#:   · RBAC/ABAC 严格模式 ⇒ 判在本层**之后**，照旧。
CONFIRM_LEVEL_EXEMPT_ENV = "CP_TOOL_CONFIRM_LEVEL_EXEMPT"

#: 受限会话 sandbox_allowed 判定开关（TASK-07 第 4 步第 2 项；**默认开**）
#: 置 0 ⇒ 退回"该字段只有声明、没有运行时消费方"的改动前状态。
SANDBOX_ALLOWED_ENFORCE_ENV = "CP_TOOL_SANDBOX_ALLOWED_ENFORCE"

#: 影子模式开关（**默认关闭**）：置 1 ⇒ 只记录"若按新规则将要求确认"，**不拦截**
#: 这是 TASK-06 §6 回滚方案要求的"先跑一个周期影子告警"落地方式
#: （v1.4 §14 执行纪律里就有"影子告警 → 白名单 → 拒绝"的先例）。
CONFIRM_LEVEL_SHADOW_ENV = "CP_TOOL_CONFIRM_LEVEL_SHADOW"

#: 执行身份（contextvar）。**空串 = 未声明**（与"声明了 human"是两件事，
#: 理由同 `_SESSION_SOURCE_VAR` 的注释：未声明要走"无身份 ⇒ 拒绝"那一支）。
_IDENTITY_VAR: contextvars.ContextVar = contextvars.ContextVar(
    "cp_tool_gate_execution_identity", default="",
)

#: 合法身份取值（与 `agent/capregistry/invoke.py::IDENTITIES` 同值域）
#: 【为什么在这里再列一次而不 import】`agent.capregistry` 会（直接或间接地）经
#: `agent.tools` 回到本模块 ⇒ 反向 import 在导入期就可能成环。一致性由
#: `tests/unit/test_confirm_level.py` 对拍锁死（与 TOOL_TYPES 的处置同一取舍）。
EXECUTION_IDENTITIES: Tuple[str, ...] = ("human", "llm", "system", "service_account")

#: 视为"非交互"的会话来源（工具面契约里 "cli" = 人在场；其余自动来源都不在场）
#: 注意 `"api"` **不算**非交互：它同时承载"模型调用"（在场的人机对话）与
#: "SA 调用"，只在**身份**维度上才能区分 ⇒ 非交互判定必须**同时**看身份与来源。
NON_INTERACTIVE_SOURCES: FrozenSet[str] = frozenset({"scheduled", "cron", "ci", "webhook"})


class _IdentityHandle:
    """``set_execution_identity()`` 的返回值（可用于 ``with``，也可手动 ``reset()``）"""

    __slots__ = ("_token",)

    def __init__(self, token: Any) -> None:
        self._token = token

    def reset(self) -> None:
        token = self._token
        if token is None:
            return
        self._token = None
        _IDENTITY_VAR.reset(token)

    def __enter__(self) -> "_IdentityHandle":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.reset()
        return False


def set_execution_identity(identity: str) -> _IdentityHandle:
    """把**当前执行上下文**的执行身份设为 ``identity``（返回可 ``with``/``reset`` 的句柄）

    取值：``human`` / ``llm`` / ``system`` / ``service_account``（空串 = 未声明）。
    与 :func:`set_session_source` 同一套线程语义：**设置点必须在真正执行的那个线程内**
    （contextvars 不跨线程继承）。非法取值**不抛异常**，而是存原值 —— 判定侧对
    "不在值域内"一律按 **未声明** 处理（fail-closed：无身份 ⇒ L1+ 拒绝）。
    """
    return _IdentityHandle(
        _IDENTITY_VAR.set(str(identity or "").strip().lower()))


def current_execution_identity() -> str:
    """读当前执行上下文的执行身份；**未声明 ⇒ 空串**（不抛异常）"""
    try:
        raw = _IDENTITY_VAR.get()
    except Exception:  # noqa: BLE001  上下文不可读 ⇒ 按"未声明身份"处理
        return ""
    return str(raw or "").strip().lower()


def reset_execution_identity(handle: Any) -> None:
    """还原执行身份（接受 :func:`set_execution_identity` 的句柄或裸 ``Token``）"""
    if handle is None:
        return
    try:
        reset = getattr(handle, "reset", None)
        if callable(reset):
            reset()
            return
        _IDENTITY_VAR.reset(handle)
    except Exception as e:  # noqa: BLE001
        logger.warning("[tool_gate] 执行身份还原失败: %s: %s", type(e).__name__, e)


def is_non_interactive(session_source: Optional[str] = None,
                       identity: Optional[str] = None) -> bool:
    """本次调用是否**非交互**（cron / CI / Webhook / 后台任务）

    【判据要**同时**看身份与来源，二者任一命中即算非交互】
      · 身份 ∈ {``system``, ``service_account``} ⇒ 非交互（这两类主体本就不在人在场）；
      · 来源 ∈ :data:`NON_INTERACTIVE_SOURCES` ⇒ 非交互（``scheduled`` 是定时任务上报的）。

    【为什么不能只看来源】`"api"` 这一来源被**两条语义完全相反**的链路共用：
    模型发起的人机对话（人在场）与 SA 的 CI 调用（无人在场）。只看来源会把前者
    误判成非交互 ⇒ 人机对话里的高危工具第一次调用就被拒而非挂单。
    【为什么不能只看身份】大量既有调用方**根本不报身份**（默认空串），
    只看身份会让定时任务（它报了 source 但没报 identity）被当成交互式而挂空单。
    """
    ident = (str(identity).strip().lower() if identity is not None
             else current_execution_identity())
    if ident in ("system", "service_account"):
        return True
    src = (str(session_source).strip().lower() if session_source is not None
           else current_session_source())
    return src in NON_INTERACTIVE_SOURCES


# ── SA 预授权钩子（**闸门内的判定，不是旁路**）────────────────────────────
# 【D1】唯一实现在 `agent/security/service_account.py::preauthorize`，
#   由它在本模块**注册**；本模块不 import 它（避免反向依赖与导入期成环）。
#   钩子签名：fn(capability: str, identity: str, args: dict, level: str) -> bool
_PREAUTH: Dict[str, Any] = {"fn": None}


def set_preauthorization_hook(fn: Optional[Any]) -> None:
    """注册 SA 预授权判定钩子（``None`` = 注销）

    预授权**不绕过闸门**：它只是本模块在 L2/L3 分支上的一条判定
    （TASK-06 §5 "✗ 让 SA 直接绕过 tool_gate" 的落地约束）。
    """
    _PREAUTH["fn"] = fn


def preauthorization_hook() -> Optional[Any]:
    """当前已注册的预授权钩子（``None`` = 未注册 ⇒ 一律视为未预授权）"""
    return _PREAUTH["fn"]


def _preauthorized(capability: str, identity: str,
                   args: Optional[Dict[str, Any]], level: str) -> bool:
    """查 SA 预授权；**无钩子 / 钩子异常 ⇒ 视为未预授权**（绝不静默放行）"""
    fn = _PREAUTH["fn"]
    if fn is None:
        return False
    try:
        return bool(fn(capability, identity, dict(args or {}), level))
    except Exception as e:  # noqa: BLE001  预授权查询失败 ⇒ fail-closed
        logger.warning("[tool_gate] SA 预授权钩子异常（按未授权处理）: %s: %s",
                       type(e).__name__, e)
        return False


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
       为真 ⇒ 默认拦截。已有人工批准 ⇒ 消费后放行（单次有效）；已被驳回 ⇒
       ``APPROVAL_REJECTED``；否则幂等挂单并返回 ``APPROVAL_REQUIRED`` + ``approval_id``。
       置 ``CP_TOOL_GATE_APPROVAL_ENFORCE=0`` 可退回"只告警不拦截"；
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

        # 0. **受限会话的 sandbox_allowed 判定**（TASK-07 第 4 步第 2 项）
        #    `data/tool_definitions/*.yaml` 的 `sandbox_allowed` 此前**没有任何运行时
        #    消费方**（只有声明/展示）；91 个 YAML 里 59 个为 false。本步让它在
        #    "受限会话"（分身沙箱/只读会话，见 `agent/subagent/sandbox.py::restricted_session`）
        #    内真的生效：声明为 false 的能力在受限会话里被拒。
        #    【为什么放在最前面】与注入防御闸门同一理由：避免为注定被拒的调用挂审批单
        #    （悬空挂单）。本步**只出 deny、不出 allow**，不可能把下面的层短路。
        #    【零影响保证】不在受限会话内时 `guard_tool_sandbox_allowed` 恒返回 None ⇒
        #    对既有调用方逐字节无影响；`CP_TOOL_SANDBOX_ALLOWED_ENFORCE=0` 可整体关闭。
        if _sandbox_allowed_enforce_enabled():
            sandbox_deny = _sandbox_allowed_outcome(name)
            if sandbox_deny is not None:
                return sandbox_deny

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
        #   该前提失效：`shell_execute`（critical）首次变成 requires_approval=true。
        #   同一语义两种行为，是缺陷而非特性：会让"补齐元数据"这种纯数据修正
        #   意外变成"关掉一项能力"。
        #   现统一为：与第 3 步共用同一开关与同一套闭环处置（_tool_approval_outcome），
        #   一处开关管两个来源，且两个来源都走"挂单 → 人工裁决 → 原样重试"。
        index = _cached_derived(DESCRIPTORS_PATH, _build_approval_index)
        cid = _approval_hit(name, index)
        if cid is not None:
            reason = ("描述符要求人工审批（%s 能力 %s 的 trust.requires_approval=true）"
                      % (DESCRIPTORS_PATH, cid))
            if _approval_enforce_enabled():
                return _tool_approval_outcome(name, args, reason)
            _warn_once(
                "approval_desc:" + name,
                "工具 %s 的%s，但 %s=0 ⇒ 本次仅告警、不拦截（删掉该环境变量即恢复审批边界）",
                name, reason, APPROVAL_ENFORCE_ENV,
            )

        # 3. **四级确认边界**（v1.4 §10.2 / TASK-06 §3 第 2 步；**唯一真相：YAML**）。
        #    取代原先的二值 `needs_approval → 挂单`：
        #      L0 免确认 / L1 摘要确认（可批量）/ L2 逐次确认（单次有效）/
        #      L3 默认禁止（须显式预授权）。
        #    `_confirm_level_outcome` 内部已处理：开关关闭（回滚）、影子模式、
        #    无身份拒绝、SA 预授权、非交互不挂单 ⇒ 这里只负责短路。
        confirm_outcome = _confirm_level_outcome(name, args, session_source)
        if confirm_outcome is not None:
            return confirm_outcome

        # 4. HITL 兜底判据 + 伦理硬规则（**只补 YAML 覆盖不到的两块**，详见各自 docstring）：
        #    - 未登记工具（YAML 无条目）⇒ HITL fail-closed 判 HIGH ⇒ 走审批（原为直接放行）；
        #    - 伦理硬规则命中（execute/extend 类工具）⇒ 升级为需审批。
        #    已登记工具在这一步不会被重复判定（元数据的唯一权威仍是 YAML）。
        fallback_reason = (_hitl_boundary(name, args)
                           or _ethics_boundary(name, args))
        if fallback_reason is not None:
            if _approval_enforce_enabled():
                # 【TASK-06】兜底路径同样要过身份/非交互判定 —— 否则它就成了
                # "另一条能挂空单的路"（非交互来源在收件箱里永远等不到裁决）。
                # 级别按 L2 处置（未登记工具，从严；`_hitl_boundary` 本就判 HIGH）。
                _fb_src = str(session_source or "").strip() or current_session_source()
                _fb_ident = current_execution_identity()
                _fb_guard = _noninteractive_guard(
                    name, args, fallback_reason, level="L2", identity=_fb_ident,
                    source=_fb_src,
                    non_interactive=is_non_interactive(_fb_src, _fb_ident))
                if _fb_guard is GUARD_PASS:
                    return None         # SA 预授权（同 `_confirm_level_outcome` 的三态）
                if _fb_guard is not GUARD_CONTINUE:
                    return _fb_guard
                return _tool_approval_outcome(name, args, fallback_reason, level="L2",
                                              identity=_fb_ident, source=_fb_src)
            _warn_once(
                "fallback:" + name,
                "工具 %s 命中兜底判据（%s），但 %s=0 ⇒ 本次仅告警、不拦截",
                name, fallback_reason, APPROVAL_ENFORCE_ENV,
            )

        # 5. 【可选，默认关闭】RBAC 严格模式：只在上面几步都未拒绝之后才追加。
        #    本步自带 fail-open 边界（见 _strict_deny）：严格层内部的任何异常都放行，
        #    但网关**明确返回 allowed=False** 时必须真的拒绝。
        if _strict_enabled():
            strict_denied = _strict_deny_or_open(name, args, session_source)
            if strict_denied is not None:
                return strict_denied
        return None
    except Exception as e:  # noqa: BLE001
        # 【TASK-06 §3 第 1 步第 4 项：治理动作 fail-closed】
        #   原实现是"任何异常一律 fail-open 放行"。对**只读/低危**动作，fail-open 是
        #   对的（闸门自身 bug 不该阻断日常读取）；但对**治理动作**（会改能力集、
        #   会写/删数据）来说，"安全判据读不到依据时放行"的代价无上界。
        #   故按工具性质**分流**：治理动作 ⇒ 拒绝（fail-closed），其余 ⇒ 放行。
        #   判据用 YAML 元数据（唯一权威）：L3 / needs_approval / plane=govern。
        #   元数据本身读不到时（`_meta_for` 返回 None）**仍按 fail-closed 处置** ——
        #   "连这个工具是不是治理动作都证不出来"恰恰是更该拒绝的情形。
        if _is_governance_action(name):
            logger.error("[tool_gate] 闸门判定异常且该工具属**治理动作** ⇒ "
                         "按 fail-closed 拒绝: %s: %s", type(e).__name__, e)
            return _deny(
                name,
                "工具闸门判定过程中发生内部错误（%s），而该工具属**治理动作**"
                "（会改变云枢自身能力集或造成不可逆后果）⇒ 按 fail-closed **拒绝**"
                "本次调用。请查看服务端日志定位后重试。" % type(e).__name__)
        logger.warning("[tool_gate] 闸门判定异常（按 fail-open 放行）: %s: %s",
                       type(e).__name__, e)
        return None


def _sandbox_allowed_enforce_enabled() -> bool:
    """受限会话 `sandbox_allowed` 判定开关（**默认开**；不在受限会话内时零影响）

    【为什么默认开是安全的】本层的第一条判据是"当前是否处于受限会话"
    （`agent/subagent/sandbox.py::in_restricted_session`，一个 contextvar）。
    既有调用方**没有一个**进入受限会话 ⇒ 打开它对既有行为逐字无影响。
    真正的影响面只在"分身/沙箱会话"里 —— 而那正是该字段的语义所指。
    """
    raw = os.environ.get(SANDBOX_ALLOWED_ENFORCE_ENV)
    if raw is None:
        return True
    return str(raw).strip().lower() in _ENABLED_VALUES


def _sandbox_allowed_outcome(func_name: str) -> Optional[Dict[str, Any]]:
    """受限会话内 `sandbox_allowed=false` ⇒ 拒绝结果；否则 None（**不抛**）

    【为什么不需要"未登记工具"的兜底】`sandbox_allowed_for` 读不到元数据时返回
    True（不缺省拒绝）——那是刻意的：本层的职责是**消费一个已声明的字段**，
    不是"对未登记工具做安全兜底"（后者由 `_hitl_boundary` 的 fail-closed 承担）。
    两层各管一件事，避免"新层顺手把旧层的判据又抄一遍"。
    """
    try:
        from agent.subagent.sandbox import guard_tool_sandbox_allowed
    except Exception as exc:  # noqa: BLE001  沙箱模块不可用 ⇒ 本层不参与判定
        logger.debug("[tool_gate] 沙箱受限会话判定不可用（跳过）: %s", exc)
        return None
    reason = guard_tool_sandbox_allowed(func_name)
    if reason is None:
        return None
    logger.warning("[tool_gate] %s", reason)
    return _deny(func_name, reason)


def _is_governance_action(func_name: str) -> bool:
    """该工具是否属**治理动作**（异常时按 fail-closed 处置的判据）

    判据（任一命中即是，全部来自 YAML 元数据这一唯一权威）：
      · `effective_confirm_level == "L3"`（含 `plane=govern` / `effect=extend` /
        `risk=critical` 三种来源）；
      · `needs_approval` 为真（含 13 个 `risk: high`）；
      · **元数据读不到** ⇒ 也返回 True。

    【为什么"读不到元数据"要判成治理动作】这是本函数唯一反直觉的一条。理由：
    异常发生时我们**证不出**这个工具是无害的读取操作。把"证不出"判成"放行"，
    等于让"元数据加载失败"成为一条绕过治理的路径（攻击面：只要能让 YAML 读失败，
    高危工具就免检）。判成治理动作只是让那次调用失败一次 —— 代价可控且有明确文案。
    """
    try:
        level, meta = _confirm_level_of(func_name)
    except Exception:  # noqa: BLE001  连判据都取不到 ⇒ fail-closed
        return True
    if meta is None:
        return True
    if level == "L3":
        return True
    return bool(getattr(meta, "needs_approval", False))


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


def _deny_approval(func_name: str, reason: str, *,
                   approval_id: str = "",
                   error_code: str = ERROR_CODE_APPROVAL_REQUIRED,
                   guidance: str = "") -> Dict[str, Any]:
    """构造"需要人工审批 / 已被驳回"的拒绝结果（治理平面审批边界专用）

    与 :func:`_deny` 的区别只在 ``error_code`` 与额外结构化字段——
    ``PERMISSION_DENIED`` 是"这个工具不许用"，``APPROVAL_REQUIRED`` 是"这个工具要先
    走审批"、``APPROVAL_REJECTED`` 是"人工已否决这一次"。调用方（如审批 UI／模型）
    据此区分"硬拒绝 / 待审批 / 已否决"，故**不能**复用前者。

    ``approval_id`` 是挂单编号（收件箱里那张单子的 id）；``guidance`` 是给模型的
    下一步指令（怎么恢复），单独成字段而不是混进 ``error``，便于调用方直接照做。
    """
    message = ("工具 %s 被集中式工具闸门拒绝: %s" % (func_name, reason))
    if error_code == ERROR_CODE_APPROVAL_REQUIRED:
        message = ("工具 %s 需要人工审批（%s）；已提交审批收件箱，"
                   "请人工确认后**重试同一次调用**（同一工具 + 同一参数）"
                   % (func_name, reason))
    elif error_code == ERROR_CODE_APPROVAL_REJECTED:
        message = "工具 %s 的该次调用已被人工否决（%s），请勿重试" % (func_name, reason)
    if approval_id:
        message += " [审批单 %s]" % approval_id
    logger.warning("[tool_gate] %s", message)
    result = {
        "ok": False,
        "blocked": True,
        "error_code": error_code,
        "error": message,
        "tool": func_name,
        "reason": reason,
    }
    if approval_id:
        result["approval_id"] = str(approval_id)
    if guidance:
        result["guidance"] = guidance
    return result


# ─────────────────────────────────────────────────────────────
# 审批闭环接线（挂单 → 人工裁决 → 恢复执行）
# ─────────────────────────────────────────────────────────────


def _current_session_key() -> str:
    """当前会话键（TraceContext 的 ``subject_id``，与 ``plan_tools`` 同一来源）

    审批单按会话绑定：A 会话批准的一次执行，不该自动授权 B 会话的同名调用。
    取不到（无上下文／观测不可用）⇒ 返回空串＝**通配**（见 ``agent/tool_approval``
    的匹配规则）：能批准的人只有人类，故"取不到会话"降级为"该批准对所有会话有效"，
    而不是把工具彻底卡死。**为什么不 import plan_tools 拿这个键**：``plan_tools``
    依赖工具注册表 ``agent.tools``，而 ``agent.tools`` 又导入本模块 ⇒ 会构成环。
    """
    try:
        from agent.observability.trace_v2 import TraceContext  # noqa: PLC0415 惰性
        ctx = TraceContext.current()
        return str(getattr(ctx, "subject_id", "") or "").strip()
    except Exception:  # noqa: BLE001 观测不可用不影响审批判定
        return ""


def _tool_approval_outcome(func_name: str, args: Optional[Dict[str, Any]],
                           reason: str, *, level: str = "",
                           tenant_id: str = "", version: str = "",
                           identity: str = "", source: str = ""
                           ) -> Optional[Dict[str, Any]]:
    """审批边界**已开启**时的最终裁决：``None`` = 放行；dict = 拒绝结果

    判定顺序（与人工在审批收件箱里的动作一一对应）：

    1. **已驳回** ⇒ ``APPROVAL_REJECTED``（明确告知模型别再重试）；
    2. **已有有效批准** ⇒ 消费（单次有效）后放行；消费失败说明这张单已被用过 ⇒
       继续往下走（重新挂单），**绝不错放**；
    3. 其余 ⇒ 幂等挂单并返回 ``APPROVAL_REQUIRED``（带 ``approval_id`` 与恢复指引）。

    【TASK-06 新增 `level`：L1 的批准**不消费**，L2/L3 的批准**消费**（单次有效）】
      `level="L1"` ⇒ 命中批准后**直接放行且不消费** —— 于是同一次批准在
      **会话 + TTL** 窗口内可反复覆盖同一工具的后续 L1 调用。这就是 §3 第 2 步第 3 项
      要求的"L1 摘要确认、**可批量确认**"（避免 35 个写/中危工具每次都点），
      也是 `tool_gate.py:769-773` 当初降级不拦的那个体验考量的正解。
      `level in ("L2","L3")` 或未声明 ⇒ 沿用既有的**单次有效**语义（消费台账）。

    **fail-closed 边界**：本函数只在这一层收紧。桥接层不可用／挂单失败时**不放行**，
    而是照常返回"需要审批"（附失败原因）——审批边界一旦开启，"证不出已批准"就不能执行；
    这与本模块其余各步的 fail-open（闸门自身 bug 不阻断执行）是**刻意的不对称**，
    理由与 ``HITLManager.assess`` 一致：安全判据读不到依据时，放行的代价无上界。
    """
    session_key = _current_session_key()
    reusable = str(level or "").strip().upper() == "L1"
    try:
        from agent.tool_approval import (  # noqa: PLC0415 惰性：审批桥接层较重
            consume, find_permission, is_rejected, request_approval,
        )
    except Exception as e:  # noqa: BLE001 桥接层不可用 ⇒ 仍按"需审批"处理（不放行）
        _warn_once("approval-bridge", "审批桥接层不可用（按需审批处理，不放行）: %s: %s",
                   type(e).__name__, e)
        _audit_confirm_decision(tool=func_name, level=level, decision="denied_bridge_down",
                                identity=identity, source=source,
                                reason="%s；审批桥接层不可用: %s" % (reason, e),
                                tenant_id=tenant_id, version=version)
        return _deny_approval(func_name, "%s；审批桥接层不可用: %s" % (reason, e))

    rejected = is_rejected(func_name, args, session_key=session_key)
    if rejected:
        _audit_confirm_decision(tool=func_name, level=level, decision="rejected",
                                identity=identity, source=source,
                                reason="%s；人工已否决" % reason,
                                tenant_id=tenant_id, version=version)
        return _deny_approval(
            func_name,
            "%s；人工已否决：%s" % (reason, rejected.get("reason") or "（未填原因）"),
            approval_id=str(rejected.get("approval_id") or ""),
            error_code=ERROR_CODE_APPROVAL_REJECTED)

    permitted = find_permission(func_name, args, session_key=session_key)
    if permitted:
        approval_id = str(permitted.get("approval_id") or "")
        if reusable:
            # ── L1：摘要确认的"批量"语义（批准不消费，会话 + TTL 内复用）──
            logger.info("[tool_gate] 工具 %s 命中 L1 会话级批准（审批单 %s，批准人 %s），"
                        "本次放行（L1 批准可复用，不消费）",
                        func_name, approval_id, permitted.get("decided_by") or "?")
            _audit_confirm_decision(
                tool=func_name, level=level, decision="approved",
                identity=identity, source=source,
                reason="%s；人工摘要确认（L1 会话级批准，可复用）" % reason,
                tenant_id=tenant_id, version=version)
            return None
        if consume(approval_id, func_name, args):
            logger.info("[tool_gate] 工具 %s 命中人工批准（审批单 %s，批准人 %s），本次放行",
                        func_name, approval_id, permitted.get("decided_by") or "?")
            _audit_confirm_decision(
                tool=func_name, level=level, decision="approved",
                identity=identity, source=source,
                reason="%s；人工逐次确认（单次有效，已消费）" % reason,
                tenant_id=tenant_id, version=version)
            return None
        # consume() 返回 False 有三种情形（见 tool_approval.consume 的 docstring）：
        # ① 已被消费过；② 台账写入失败；③ 拿不到台账锁/等锁超时。三者一律 fail-closed，
        # 真实原因（含锁超时的持有者 pid）已由 tool_approval 侧的 WARNING 先打出，
        # 这里不再断言"已被消费过"——那是把归因写死，会掩盖锁超时这条新分支。
        logger.warning("[tool_gate] 审批单 %s 未能消费（单次有效；原因见上方 tool_approval "
                       "的 WARNING：已消费过 / 台账写入失败 / 台账锁超时），改为重新挂单",
                       approval_id)

    requested = request_approval(func_name, args, reason=reason, session_key=session_key,
                                 source=str(_env_str("CP_PERMISSION_SESSION_SOURCE") or ""))
    if not requested.get("ok"):
        _audit_confirm_decision(tool=func_name, level=level, decision="request_failed",
                                identity=identity, source=source,
                                reason="%s；挂单失败: %s" % (reason, requested.get("error")),
                                tenant_id=tenant_id, version=version)
        return _deny_approval(func_name, "%s；挂单失败: %s"
                              % (reason, requested.get("error") or "未知原因"))
    approval_id = str(requested.get("approval_id") or "")
    state = str(requested.get("state") or "")
    if state and state != "pending_review":
        # 审批流被停用（APPROVAL_ENABLED=0）时 `ApprovalFlow.submit()` 会**直接放行**
        # 并落一条 merged 记录 ⇒ 收件箱里不会出现任何待办，人根本无从批准。
        # 此时若照常返回"已提交审批收件箱、请人工确认后重试"，就是发了一张**永远等不到**
        # 的单号（模型无限重试、人看不到东西）。必须显式说清并给出两条出路。
        return _deny_approval(
            func_name,
            "%s；审批流当前未处于待审状态（record state=%s，通常是 APPROVAL_ENABLED=0 "
            "关闭了审批流）⇒ 收件箱里不会出现待办，没有人能批准它" % (reason, state),
            approval_id=approval_id,
            guidance=("请二选一：① 启用审批流（APPROVAL_ENABLED=1）后重试；"
                      "② 若确实不需要审批，显式关闭审批边界（%s=0）。"
                      "在此之前该工具不可用。" % APPROVAL_ENFORCE_ENV))
    reused = bool(requested.get("reused"))
    return _deny_approval(
        func_name, reason, approval_id=approval_id,
        guidance=("该次调用已在审批收件箱挂单%s。人工裁决入口（二选一）："
                  "① 浏览器打开 %s ；② 命令行 \"pwsh -File scripts/approve_tool_call.ps1 "
                  "-Approve <审批单号>\"。裁决后**原样重试这一次调用**"
                  "（同一工具 + **逐字相同**的参数；参数差一个字就算另一次调用、需另挂一单）；"
                  "批准为单次有效，L1 摘要确认在会话+时效内可复用。"
                  % ("（复用先前挂单）" if reused else "", APPROVAL_CONSOLE_URL)))


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
    """审批边界开关：**默认开启**；显式设为 0/false/no/off 才关闭

    【2026-09-17 默认值反转】原先是"未设置＝不拦截"。审批闭环（挂单 → 人工在
    审批收件箱裁决 → 原样重试即放行，单次有效）接通之后，"不拦截"不再有理由：
    拦截已是一条**能走通**的路径，而不是"永远失败"。故默认改为拦截，
    回滚方式是设 ``CP_TOOL_GATE_APPROVAL_ENFORCE=0``（不写任何数据文件、不改代码）。
    读取异常按"启用"处理：审批边界宁可多问一次人工，不可因读环境变量失败而静默放行。
    """
    try:
        raw = _env_str(APPROVAL_ENFORCE_ENV)
    except Exception:  # noqa: BLE001 读环境失败 ⇒ 按启用（fail-closed 于本层）
        return True
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() in _ENABLED_VALUES


def _confirm_level_enforce_enabled() -> bool:
    """确认分级强制开关（**默认开启**；显式 0/false/no/off 才退回旧行为）

    【为什么默认开启】TASK-06 的核心缺陷就是"13 个 `risk: high` 工具完全不触发确认"，
    默认关闭等于缺陷仍在（只是多了一个没人打开的开关）。回滚 = 设该变量为 0。
    读取异常按"启用"处理（与 `_approval_enforce_enabled` 同纪律：审批边界宁可多问
    一次人工，不可因读环境变量失败而静默放行）。
    """
    try:
        raw = _env_str(CONFIRM_LEVEL_ENFORCE_ENV)
    except Exception:  # noqa: BLE001
        return True
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() in _ENABLED_VALUES


def _confirm_level_shadow_enabled() -> bool:
    """影子模式开关（**默认关闭**）：开启时只告警不拦截（TASK-06 §6 迁移第一步）"""
    try:
        raw = _env_str(CONFIRM_LEVEL_SHADOW_ENV)
    except Exception:  # noqa: BLE001  读不到按"非影子"处理（默认口径）
        return False
    if raw is None or not raw.strip():
        return False
    return raw.strip().lower() in _ENABLED_VALUES


# ── 豁免名单变更留痕（A2/R2 护栏）────────────────────────────────────────────
#
# 【为什么放在消费侧】写入侧（`agent/tool_exemptions.py` → 开关中心的
# `settings.change`）只覆盖「经界面/服务改」的通路。A2 实测（见 A2.md §S1）：名单的
# **生效值**还可以由「直改 `data/ui_settings.json`」「改 `.env`/`config.yaml` 后重启」
# 「进程环境变量被注入」改变 —— 这三条通路**一条记录都没有**。本模块是名单的
# **唯一消费者**，在消费点对拍「链上已知值 vs 当前生效值」即可把三条通路一并补上。
#
# 【成本】热路径只多一次字符串比较：同一个生效值只在**首次**对拍（进程内缓存），
# 对拍本身是两次索引查询（`ORDER BY seq DESC LIMIT n`；D1 已把读路径从全表扫改为
# 索引读，实测 72,289 行 1.4 ms）。
_UNSET: Any = object()
_EXEMPT_WATCH_LOCK = threading.Lock()
#: 进程内「上一次已对拍过的生效值」（哨兵 `_UNSET` = 本进程尚未对拍过）
_EXEMPT_WATCH: Dict[str, Any] = {"raw": _UNSET}


def _normalize_exempt_value(raw: Any) -> str:
    """归一化名单字符串：去空白、丢空项、**保留书写顺序**

    （``" fan_out ,, delegate "`` → ``"fan_out,delegate"``）

    【为什么不用集合比较】本函数的产出要写进审计记录的 `old_value`/`new_value`，
    必须**逐字**可复核 —— 顺序变了也是一次真实变更。这里只做「去空白/丢空项」，
    与 :func:`_exempt_tools` 解析名单时的宽容度对齐。
    """
    parts = [p.strip() for p in str(raw or "").split(",")]
    return ",".join(p for p in parts if p)


def _chain_baseline_exempt() -> Tuple[Optional[str], bool]:
    """链上「名单上一次被记录过的生效值」→ ``(值, 是否读得到链)``

    无记录 ⇒ ``(None, True)``；读链失败 ⇒ ``(None, False)``（调用方据此如实标注
    `baseline_readable=false`，而不是假装基线是空名单）。

    两个来源（**优先消费侧自己那条**）：
      · `tool.confirm.exempt_changed` 的叶子 `new_value`（本模块写的，永远是最新的）；
      · 回落到写入侧 `settings.change`（subject=``setting:<KEY>``，值在嵌套的
        ``payload.new`` 里）—— **只在链上还没有消费侧记录时**才会走到，作用是
        「开关中心已经记过的那次变更不要再记第二遍」（避免同一事实两条记录）。
    """
    try:
        from agent.audit.facade import get_audit  # noqa: PLC0415 惰性：读路径不进导入期
        entries = get_audit().recent(5, action=EXEMPT_CHANGE_ACTION)
    except Exception as e:  # noqa: BLE001  读判定不出来 ⇒ 如实报告不可读
        logger.debug("[tool_gate] 豁免名单基线读取失败: %s: %s", type(e).__name__, e)
        return None, False
    for entry in reversed(entries):
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        if "new_value" in payload:
            return str(payload.get("new_value") or ""), True
    try:
        from agent.audit.facade import get_audit  # noqa: PLC0415
        subject = _SETTINGS_SUBJECT_PREFIX + CONFIRM_LEVEL_EXEMPT_ENV
        for entry in reversed(get_audit().recent(50, action=_SETTINGS_CHANGE_ACTION)):
            if str(getattr(entry, "subject", "") or "") != subject:
                continue
            body = entry.payload if isinstance(entry.payload, dict) else {}
            nested = body.get("payload") if isinstance(body.get("payload"), dict) else {}
            return str(nested.get("new") or ""), True
    except Exception as e:  # noqa: BLE001
        logger.debug("[tool_gate] 豁免名单写入侧基线读取失败: %s: %s",
                     type(e).__name__, e)
        return None, False
    return None, True


def _exempt_override_actor() -> Tuple[str, str]:
    """覆盖层里该键的写入者与时间 → ``(actor, updated_at)``；读不到 ⇒ ``("", "")``

    读的是**开关中心自己的 store**（`agent/settings/overrides.py`，认 `CP_UI_SETTINGS_PATH`）
    —— 本模块不自己拼路径，避免出现「第二份覆盖层解析口径」。
    """
    try:
        from agent.settings.overrides import get_override_store  # noqa: PLC0415
        rec = get_override_store().get(CONFIRM_LEVEL_EXEMPT_ENV)
    except Exception as e:  # noqa: BLE001  读不到 ⇒ 如实留空（不猜操作者）
        logger.debug("[tool_gate] 覆盖层记录读取失败: %s: %s", type(e).__name__, e)
        return "", ""
    if rec is None:
        return "", ""
    return (str(getattr(rec, "actor", "") or ""),
            str(getattr(rec, "updated_at", "") or ""))


def _exempt_levels(names: Any) -> Dict[str, str]:
    """名单里每个工具的**生效确认级**（写进变更记录："豁免了一个 L2/L3"必须一眼可见）

    【为什么把级别一起记】S1 事故的审计难点不是「名单变了」，而是「变的是哪一级的
    能力」—— `fan_out`（L2/effect=execute/risk=high）被列进名单这件事，只有在
    变更记录里点名级别，才不需要事后翻 323 条决策记录去反推。
    """
    out: Dict[str, str] = {}
    for name in names:
        key = str(name or "").strip()
        if not key:
            continue
        try:
            level, _meta = _confirm_level_of(key)
        except Exception:  # noqa: BLE001  查不到级别 ⇒ 如实记 "?"（不猜）
            level = ""
        out[key] = str(level or "?")
    return out


def _resolver_effective_value() -> Optional[Tuple[str, str]]:
    """开关中心口径的**生效值** → ``(值, 来源)``；解析器不可用 ⇒ ``None``

    【为什么不能只看 ``os.environ``（A2 实施期实测到的一次误报）】进程环境变量只是
    「覆盖层被**应用之后**」的结果：一个没有调用
    ``agent/settings/bootstrap.py::apply_overrides()`` 的进程（CLI 脚本、子进程、单测）
    读到的是空值，而 ``data/ui_settings.json`` 里其实写着 ``fan_out,delegate`` ⇒ 只看 env
    会把「本进程没应用覆盖层」误报成「名单被清空了」。故本函数用**开关中心自己的解析器**
    （env > 覆盖层 > config > 默认，见 ``agent/settings/resolver.py:55``）取操作员侧的生效值，
    与链上已知值比对；不一致才记。**只在 env 值与链上不一致时才调用**（热路径零额外开销）。
    """
    try:
        from agent.settings.resolver import resolve  # noqa: PLC0415 惰性：解析器较重
        res = resolve(CONFIRM_LEVEL_EXEMPT_ENV)
    except Exception as e:  # noqa: BLE001  解析器不可用 ⇒ 交调用方退回 env 值
        logger.debug("[tool_gate] 豁免名单生效值解析失败: %s: %s", type(e).__name__, e)
        return None
    if res is None:
        return None
    return (str(getattr(res, "value", "") or ""),
            str(getattr(res, "source", "") or ""))


def _record_exempt_change_if_needed(raw_effective: str) -> None:
    """生效值变了就往审计链写一条 ``old → new``（**消费侧留痕**，A2/R2）

    【判据】操作员侧生效值 ≠ 链上已知值 ⇒ 记一条。相等、或链上从无记录且当前为空 ⇒
    直接返回（默认态不刷噪声）。于是「经开关中心改」的那次只留 `settings.change` 一条，
    「绕过开关中心改」的那次才由本函数补记 —— 同一变更不会出现两条记录。

    【两道防误报】
      ① 进程环境变量 ≠ 链上已知值时，**先问开关中心的解析器**要生效值（见
         :func:`_resolver_effective_value`）—— 只有操作员侧生效值也真的变了才记；
      ② 记的是**解析器口径**的生效值（`new_value`），进程环境变量另存
         `process_env_value` 便于分辨「名单变了」与「这个进程没应用覆盖层」。

    【绝不静默】写失败一律 `logger.error`（与 :func:`_audit_confirm_decision` 同纪律：
    留痕失败不该让调用变失败，但必须可被日志告警捕获）。

    【为什么先入缓存再落审计】并发下同一变更只会有一条记录；代价是「写失败不重试」
    （避免每次调用都去查一遍链）。这一取舍与「宁可少记也不能刷屏」一致。
    """
    env_value = _normalize_exempt_value(raw_effective)
    with _EXEMPT_WATCH_LOCK:
        if _EXEMPT_WATCH["raw"] == env_value:
            return
        _EXEMPT_WATCH["raw"] = env_value

    baseline, readable = _chain_baseline_exempt()
    if readable:
        old_value = _normalize_exempt_value(baseline)
        if baseline is None and not env_value:
            return                                  # 名单为空且链上无记录 ⇒ 无事实
        if old_value == env_value:
            return                                  # 与链上已知值一致 ⇒ 无变更
    else:
        old_value = "<unknown>"                     # 读不到基线：如实标注，不假装是空名单

    resolved = _resolver_effective_value()
    if resolved is not None:
        new_value, value_source = _normalize_exempt_value(resolved[0]), resolved[1]
    else:
        new_value, value_source = env_value, "process_env"
    if readable and new_value == old_value:
        # 操作员侧生效值没变 ⇒ 本次差异只是「本进程没应用覆盖层」⇒ **不记**（防误报 ①）
        logger.debug("[tool_gate] 豁免名单：本进程 env=%r 而生效值=%r（未变）⇒ 不记变更",
                     env_value, new_value)
        return

    if readable:
        old_items = set(old_value.split(",")) if old_value else set()
        new_items = set(new_value.split(",")) if new_value else set()
        added = sorted(new_items - old_items)
        removed = sorted(old_items - new_items)
    else:
        added, removed = [], []

    override_actor, override_at = _exempt_override_actor()
    try:
        from agent.audit.chain import SOURCE_SYSTEM  # noqa: PLC0415
        from agent.audit.facade import record as _audit_record  # noqa: PLC0415
        _audit_record(
            action=EXEMPT_CHANGE_ACTION,
            actor=override_actor or None,          # 无覆盖层记录 ⇒ 交 facade 解析（UI 上下文/系统）
            subject=_SETTINGS_SUBJECT_PREFIX + CONFIRM_LEVEL_EXEMPT_ENV,
            extra={
                "setting_key": CONFIRM_LEVEL_EXEMPT_ENV,
                "old_value": old_value,
                "new_value": new_value,
                "process_env_value": env_value,
                "value_source": value_source,
                "actor": override_actor or "",
                "actor_from": "override_layer" if override_actor else "unresolved",
                "override_updated_at": override_at,
                "added": added,
                "removed": removed,
                "levels": _exempt_levels(new_value.split(",")),
                "baseline_readable": bool(readable),
                "observed_by": "agent.tool_gate",
            },
            source=SOURCE_SYSTEM)
    except Exception as e:  # noqa: BLE001  留痕失败不影响执行，但绝不静默
        logger.error("[tool_gate] 豁免名单变更审计写入失败（old=%r new=%r）: %s: %s",
                     old_value, new_value, type(e).__name__, e)


def _exempt_tools() -> FrozenSet[str]:
    """``CP_TOOL_CONFIRM_LEVEL_EXEMPT`` 解析 ⇒ 归一化查找键集合（读不到/为空 ⇒ 空集）

    每一项都经 ``_query_keys`` 展开 ⇒ ``delegate`` 与 ``cp.builtin.delegate`` 两种写法
    都能命中（写名单的人不必知道闸门内部用哪个键比对）。
    """
    try:
        raw = _env_str(CONFIRM_LEVEL_EXEMPT_ENV)
    except Exception:  # noqa: BLE001  读不到 ⇒ 不豁免（从严的一侧）
        return frozenset()
    if not raw:
        return frozenset()
    keys: Set[str] = set()
    for part in str(raw).split(","):
        text = part.strip()
        if text:
            keys |= _query_keys(text)
    keys.discard("")
    return frozenset(keys)


def _is_confirm_level_exempt(func_name: str) -> bool:
    """本次工具是否在操作员豁免名单里（名单为空时**逐字零影响**）

    【顺带的副作用（A2/R2）】本函数是名单的**唯一消费点**，故在此对拍"当前生效值 vs
    链上已知值"并补记变更留痕（见 :func:`_record_exempt_change_if_needed`）。
    观察点选在这里而不是模块导入期：只有闸门**真的要用这份名单**时它才是"生效"的，
    「装了个开关但从没被消费过」不该产生治理记录。
    """
    raw = ""
    try:
        raw = _env_str(CONFIRM_LEVEL_EXEMPT_ENV) or ""
    except Exception as e:  # noqa: BLE001  读不到 ⇒ 按空名单（与 _exempt_tools 同向）
        logger.debug("[tool_gate] 豁免名单读取失败: %s: %s", type(e).__name__, e)
    try:
        _record_exempt_change_if_needed(raw)
    except Exception as e:  # noqa: BLE001  留痕绝不阻断判定的主路径（但其内部已会记日志）
        logger.debug("[tool_gate] 豁免名单变更留痕失败: %s: %s", type(e).__name__, e)
    exempt = _exempt_tools()
    if not exempt:
        return False
    return bool(exempt & _query_keys(func_name))


def _confirm_level_of(func_name: str) -> Tuple[str, Any]:
    """取该工具的**生效**确认级别与元数据 → ``(level, meta)``

    ``level`` 为空串 = 未登记/无元数据（调用方按既有 fail-open 口径处置）。
    """
    meta = _meta_for(func_name)
    if meta is None:
        return "", None
    level = str(getattr(meta, "effective_confirm_level", "") or "").strip().upper()
    return (level, meta)


def _audit_confirm_decision(*, tool: str, level: str, decision: str,
                            identity: str, source: str, reason: str,
                            tenant_id: str = "", version: str = "",
                            actor: str = "") -> None:
    """把一次确认决策落进**审计链**（v1.4 §12；TASK-06 §3 第 2 步第 5 项）

    【为什么必须进 audit_chain 而不是留在 `approval_records.jsonl`】
      TASK-00 已把 `data/approval_records.jsonl` 标为"自相矛盾"：它被**整文件重写**
      （`agent/skills_mgmt/approval.py:1001-1004`），与 `tool_approval.py:30`
      "绝不改写/删除任何记录"的声明冲突，且**不在哈希链上** ⇒ 可被无声篡改。
      `agent/audit/chain.py` 是仓库质量最高的设施（真实哈希链 + Merkle + ed25519），
      把确认决策并进去才满足"审计可区分"（E9）与"审批记录并入 audit_chain"。

    【🔴 2026-09-20 实测修复：`source` 原为 `"tool_gate"`，而它不是合法值】
      `agent/audit/chain.py:131` 的 `SOURCES = {agent, ui, system, migration}`，
      `append(source="tool_gate")` 会抛 `AuditEntryError: 非法 source`。而本函数
      **整个吞异常**（设计如此：留痕失败不该让已放行的调用变失败）⇒ 后果是
      **每一条确认决策的审计都写不进去，只在 ERROR 日志里留痕**：
      E9（"三种身份在审计记录里可明确区分"）与交付物 #6/#7（"审批记录并入
      audit_chain"）表面上"已实现"，实际一条记录都没有。
      这正是 D12 说的那类假绿 —— **单测全绿、生产 100% 失效**（本文件的假替身
      形式是"测试 monkeypatch 掉了 `_audit_confirm_decision`，于是测的是替身不是它"）。
      修法：用链上的合法来源 `SOURCE_SYSTEM`（网关是**平台内部**的治理执行体，
      与 `system` 的语义一致；`agent` 表示智能体自身动作，`ui` 表示界面动作）。
      并由 `tests/unit/test_confirm_level.py::TestAuditLandsOnTheChain` 用**真实**
      审计链复测一次（不 monkeypatch 本函数）。

    【🔴 2026-09-20 实测修复 ②：原来直接 `get_audit_chain()`，**忽略了 `AUDIT_DB_PATH`**】
      `agent/audit/chain.py::get_audit_chain(db_path=None)` 会落到
      `_resolve_path(None)` ⇒ **硬编码的默认路径** `data/audit/audit_chain.db`；
      它**不读** `AUDIT_DB_PATH` 环境变量（读那个变量的是 `agent/audit/facade.py`）。
      后果两条，均已实测：
        ① **测试写生产审计链**：`tests/conftest.py` 把 `AUDIT_DB_PATH` 指向临时目录，
           但对本函数无效 ⇒ 任何触发确认决策的用例都会往**生产的**
           `data/audit/audit_chain.db` 追加记录（实测：`test_tool_approval_e2e.py`
           留下了 `subject='probe_approval_e2e_tool'` / `'ext_install'` 的 4 条记录，
           本任务的审计样本 3 条也在其中）⇒ 违反 D6「不碰生产数据」，
           且调用方**无法通过配置把审计重定向**（多实例/多环境部署时会写到同一个库）。
        ② 根路径同样不受控：`AuditChain.__init__` 的 `roots_path` 也只认参数不认环境变量
           ⇒ 每日 Merkle 根会落到生产 `data/audit/daily_roots.jsonl`。
      ⇒ 改为走仓库的**唯一写入入口** `agent/audit/facade.py::record()`：它按
        `AUDIT_DB_PATH` / `AUDIT_ROOTS_PATH` / `AUDIT_SIGNING_KEY` 解析路径、做载荷脱敏、
        并保证与 UI 面同表（P7.2-24 审计平权）。这样"测试隔离"与"部署可重定向"
        两件事才真的成立。

    【E9 可区分性】三条字段组合足以区分 Task-06 §1 的三种情形：
      · 人逐次确认     → ``identity=human`` + ``decision=approved``
      · SA 凭预授权    → ``identity=service_account`` + ``decision=preauthorized``
      · 无身份被拒     → ``identity=""`` + ``decision=denied_no_identity``

    【为什么整个函数吞异常】审计是**治理留痕**，不是执行前置条件；写审计失败
    不该让一次已判定放行的调用变成失败（那是把可观测性做成可用性风险）。
    但它**绝不静默**：失败一律 ``logger.error``（可被日志告警捕获）。
    """
    try:
        # `SOURCE_SYSTEM` 是链上的来源常量（facade 不重导出它），
        # 写入仍走 facade 的 `record()`（唯一入口，且按环境变量解析路径）
        from agent.audit.chain import SOURCE_SYSTEM  # noqa: PLC0415
        from agent.audit.facade import record  # noqa: PLC0415
        record(
            action="tool.confirm_decision",
            actor=str(actor or identity or "unknown"),
            subject=str(tool or ""),
            # 【为什么用 `extra` 而不是 `payload`】facade 的 `payload=` 会**包一层信封**
            # （实测落成 `{"schema":…, "actor_source":…, "payload":{…}}`）⇒ 确认字段会被嵌到
            # 第二层，查询/盘点时要多剥一层。`extra` 的语义正是"追加到 payload 的**叶子**字段"
            # ⇒ 四个必填字段（confirm_level/decision/identity/tenant_id）落在顶层，
            # 与 v1.4 §12 的字段纪律一致，且经同一套脱敏。
            extra={
                "confirm_level": str(level or ""),
                "decision": str(decision or ""),
                "identity": str(identity or ""),
                "session_source": str(source or ""),
                "tenant_id": str(tenant_id or ""),
                "tool_version": str(version or ""),
                "reason": str(reason or "")[:500],
            },
            source=SOURCE_SYSTEM,
        )
    except Exception as e:  # noqa: BLE001  留痕失败不影响执行（但绝不静默）
        logger.error("[tool_gate] 确认决策审计写入失败（决策=%s 工具=%s）: %s: %s",
                     decision, tool, type(e).__name__, e)


def _meta_field(meta: Any, name: str, default: Any = "") -> Any:
    """安全取元数据字段（``meta`` 可能是 ``None`` 或非预期对象）"""
    try:
        return getattr(meta, name, default)
    except Exception:  # noqa: BLE001
        return default


def _confirm_level_outcome(func_name: str, args: Optional[Dict[str, Any]],
                           session_source: Optional[str] = None,
                           meta_override: Any = None
                           ) -> Optional[Dict[str, Any]]:
    """**四级确认**的统一裁决：``None`` = 放行；dict = 拒绝结果

    这是 TASK-06 §3 第 2 步的落点，取代原先的二值 `needs_approval → 挂单`。
    判定分支（顺序即优先级）：

    1. 未登记元数据 / 级别为空 / **L0** ⇒ 放行（L0 免确认，E2 的正面判据）；
    2. **审批边界总开关关闭**（``CP_TOOL_GATE_APPROVAL_ENFORCE=0``）⇒ 告警一次后放行
       —— 见下「两个开关的从属关系」（2026-09-20 修复的真实缺陷）；
    3. 分级开关关闭（``CP_TOOL_CONFIRM_LEVEL_ENFORCE=0``）⇒ 告警一次后放行
       （只回滚 TASK-06 **新加**的那部分：`risk: high` → L2）；
    3. **影子模式** ⇒ 告警一次"若按新规则将要求确认"，**不拦截**（迁移第一步）；
    4. **身份不可知**（既非交互来源、也无任何身份声明）⇒ **拒绝**，不挂单
       （E14："无身份调用时被拒绝（而非放行）"）；
    5. **SA 预授权命中** ⇒ 放行并落审计 ``decision=preauthorized``（E9/交付物 #8）；
    6. **非交互且无预授权** ⇒ **拒绝**并给出可操作出路，**绝不挂单**
       （E5："非交互不挂空单、收件箱无悬空待办"）；
    8. 其余（交互路径）⇒ 交给 :func:`_tool_approval_outcome`，
       L1 用**可复用**批准，L2/L3 用**单次有效**批准。

    【🔴 两个开关的从属关系（2026-09-20 修复的**真实缺陷**，不是措辞问题）】
      · CP_TOOL_GATE_APPROVAL_ENFORCE = **审批边界总开关**（既有，默认开启）。
        它管的是"这个闸门还要不要拦人"这一件事本身 ⇒ **必须**同时管住
        confirm_level 这一层。原实现漏了这一步，后果三条，全部实测：
        ① **既有回滚开关失效**：tests/unit/test_tool_approval_e2e.py:229 钉着
           "置 0 ⇒ 一处环境变量完成回滚"；而它对新加的 L1/L2/L3 层**无效果**
           ⇒ 关掉它之后 13 个 high 工具**仍然**被拦，操作员会以为"已经回滚了"。
        ② **测试基线被穿透**：tests/conftest.py:207 把本变量定为**会话基线**（置 0），
           理由是"大量单测直接 agent.tools.call() 验证**调用机制**，不该测到治理网"。
           漏掉这一层后，凡被测工具恰好是 high 的单测都会被新层拦下 ⇒ 一次改动打红
           30+ 条与本任务无关的既有测试（test_tool_gate_strict.py 等）。
        ③ **两套开关语义重叠**：总开关关着、分级开关开着时，"审批边界"到底是开还是关
           **没有唯一答案**（D1 禁止的第二真相源）。
      · CP_TOOL_CONFIRM_LEVEL_ENFORCE = **分级层的窄回滚**（TASK-06 新增，默认开启）。
        置 0 ⇒ 退回改动前的口径：只有旧的 needs_approval 集合
        （govern / extend / critical）挂单，13 个 high **不再**进确认流。
      ⇒ 两者是**从属**关系（总开关 ⊃ 分级开关），而不是并列。单一结论：
        "总开关为 0 ⇒ 审批边界整体不拦"；"总开关为 1 且分级开关为 0 ⇒ 只有新加的
        high → L2 那一条被回滚"。由
        tests/unit/test_confirm_level.py::TestTwoSwitchesAreNested 对拍锁死。

    【🔴 confirm_level **不**受策略/描述符文件缺失影响（任务 B 的裁决）】
      permission_policies.json / descriptors.json 是**运行期策略文件**，缺了它们
      那条规则**读不到依据** ⇒ fail-open（放行）是对的。
      而 confirm_level 是**设计期就声明在能力 YAML 里的策略**（D1 的单一真相源），
      它**不依赖**那两个文件 ⇒ 不因它们缺失而失效。
      判据的不对称是刻意的：**"依据读不到"≠"策略不存在"**；让"删掉一个文件"成为
      "13 个高危工具免确认"的开关，等于给治理面开一条无声旁路（攻击面）。
      要关闭这一层，必须由操作员**显式**写出总开关/分级开关为 0（可审计的动作），
      而不是靠文件缺失（不可审计的意外）。由
      tests/unit/test_confirm_level.py::TestFailOpenDoesNotWeakenConfirmLevel 锁定。
    """
    if meta_override is not None:
        # 【A2/S3：**声明式能力**的口子】`data/tool_definitions/*.yaml` 里没有条目的能力
        #   （技能脚本执行面）走 `check_declared_capability` 进来：级别由调用方**声明**的
        #   三轴（plane/effect/risk）派生，而不是查 YAML。级别**不是**调用方传进来的 ——
        #   派生仍由 `check_declared_capability` 用 `derive_confirm_level` 的唯一口径做。
        level = str(_meta_field(meta_override, "effective_confirm_level", "")
                    or "").strip().upper()
        meta = meta_override
    else:
        level, meta = _confirm_level_of(func_name)
    if not level or level == "L0":
        return None

    source = (str(session_source).strip() if session_source else "") \
        or current_session_source()
    identity = current_execution_identity()
    tenant_id = str(_meta_field(meta, "tenant_id", "") or "")
    version = str(_meta_field(meta, "version", "") or "")
    reason = ("confirm_level=%s（plane=%s, effect=%s, risk=%s）"
              % (level, _meta_field(meta, "plane", "?"),
                 _meta_field(meta, "effect", "?"), _meta_field(meta, "risk", "?")))
    # 【2026-09-20 契约衔接：伦理硬规则**合并进**确认理由，而不是被新层短路掉】
    #   顺序问题：本层（第 3 步）排在 HITL/伦理兜底（第 4 步）**之前**，而
    #   `_ethics_boundary` 只作用于 `effect ∈ {execute, extend}` 的工具 ——
    #   按 `derive_confirm_level`，这两类效果**至少是 L1**（execute 与 write 同等对待）
    #   ⇒ 新层一旦先拦，第 4 步的伦理判定对**已登记工具**就再也不会被求值，
    #   那 6 条自称"不可突破的硬约束"重新变成死代码（`test_tool_gate_fallback.py::
    #   TestEthicsRulesAreLive` 的原始目的正是防这件事）。
    #   合并而非短路：拦截结果不变（都是"要人确认"），但**人看到的理由**从
    #   "L2 逐次确认"变成 "L2 逐次确认 + 命中伦理硬规则 E003" —— 后者才是他
    #   真正需要看到的（否则会凭"常规写文件"的印象点批准）。
    _ethics = _ethics_boundary(func_name, args)
    if _ethics:
        reason = "%s；并%s" % (reason, _ethics)

    # ② 审批边界总开关（**必须先判**：它是"要不要拦"的总闸，从属关系见 docstring）
    if not _approval_enforce_enabled():
        _warn_once(
            "confirm-level-master-off",
            "工具 %s 的 %s 要求确认，但审批边界总开关 %s=0 ⇒ 本次仅告警、不拦截"
            "（这是**显式**回滚；置 1 即恢复，与「策略文件缺失」无关）",
            func_name, level, APPROVAL_ENFORCE_ENV)
        return None

    if not _confirm_level_enforce_enabled():
        _warn_once(
            "confirm-level-off",
            "工具 %s 的 %s 要求确认，但分级开关 %s=0 ⇒ 退回旧口径"
            "（仅旧的 needs_approval 集合挂单；审批边界总开关仍为开）",
            func_name, level, CONFIRM_LEVEL_ENFORCE_ENV)
        return None

    # ①-b **操作员豁免名单**（``CP_TOOL_CONFIRM_LEVEL_EXEMPT``，默认空 ⇒ 零影响）
    #   位置刻意排在"分级开关"之后、"影子模式/身份判定"之前：
    #     · 总开关（要不要拦）仍然最优先 —— 豁免不能把"根本不拦"变成"换个理由拦"；
    #     · 排在身份判定之前是有意的：豁免的对象是**工具**（这个动作不再要人确认），
    #       与"谁在调用"无关；否则同一工具在有身份时放行、无身份时被拒，语义就飘了。
    #   留痕：写一条 decision=exempted 的确认决策（谁在什么时候豁免了什么）——
    #   放宽本身必须是**可审计的动作**，不是静默旁路。
    if _is_confirm_level_exempt(func_name):
        # 【A2/R2：**L3 不可被豁免**（本轮新增的硬规则）】理由三条，逐条可核：
        #   ① 语义自相矛盾：L3 的定义是「默认禁止，仅**显式预授权**（SA + scope）才能」
        #      「执行」；一份「免确认名单」能把它变成「人来点一下也不用」，那是把禁止改写成
        #      允许，不是「放宽摘要确认」。L2（逐次确认）与之不同：豁免它仍是「本次不问了」，
        #      语义自洽 ⇒ **L2 保持可豁免**（论证见 docs/audit_skill_governance/A2.md §S1）。
        #   ② 覆盖面为 0：实测 10 个 L3 工具**全部**被 data/descriptors.json 的
        #      trust.requires_approval 描述符门控，而描述符判定在本层**之前** ⇒ 今天把 L3
        #      写进名单也够不着它。本规则因此**当前零行为影响**；它防的是「描述符条目被删/
        #      被改」之后那份名单突然生效（攻击面：改一个数据文件即可放开一个 L3）。
        #   ③ 界面不会因此说谎：agent/tool_exemptions.py::candidates() 对这 10 个 L3 工具
        #      本就返回 exemptable=false + blocked_reason（描述符门控）⇒ 闸门忽略 L3 豁免
        #      与界面显示**逐条一致**，不制造「点了没反应」的假绿灯。
        #   【为什么不能照搬到 L2】10 个 L2 工具**都不**被描述符门控，界面把它们标成可豁免
        #      ⇒ 闸门单方面忽略 L2 豁免会制造 10 处假绿灯。要禁止 L2 被豁免，必须同时改
        #      agent/tool_exemptions.py（本卡文件范围之外），故本轮不做。
        if level == "L3":
            _warn_once(
                "confirm-level-exempt-L3:" + func_name,
                "工具 %s 命中豁免名单 %s，但它是 **L3（默认禁止，仅显式预授权可执行）**"
                " ⇒ **忽略该条豁免**，照常走确认流程。L3 的放宽只能走 SA 预授权（SA + scope）。",
                func_name, CONFIRM_LEVEL_EXEMPT_ENV)
        else:
            _warn_once(
                "confirm-level-exempt:" + func_name,
                "工具 %s 命中豁免名单 %s ⇒ 免摘要确认直接放行（本应 %s）。"
                "黑名单 / 描述符 requires_approval / 伦理硬规则 / 严格模式均不受影响。",
                func_name, CONFIRM_LEVEL_EXEMPT_ENV, level)
            _audit_confirm_decision(
                tool=func_name, level=level, decision="exempted",
                identity=identity, source=source,
                reason="%s；操作员豁免名单命中（%s）" % (reason, CONFIRM_LEVEL_EXEMPT_ENV),
                tenant_id=tenant_id, version=version)
            return None

    if _confirm_level_shadow_enabled():
        _warn_once(
            "confirm-shadow:" + func_name,
            "【影子模式】工具 %s 按新规则将要求 %s，但 %s=1 ⇒ 本次只告警、不拦截"
            "（%s）", func_name, level, CONFIRM_LEVEL_SHADOW_ENV, reason)
        _audit_confirm_decision(tool=func_name, level=level, decision="shadow_alert",
                                identity=identity, source=source, reason=reason,
                                tenant_id=tenant_id, version=version)
        return None

    non_interactive = is_non_interactive(source, identity)
    guard = _noninteractive_guard(
        func_name, args, reason, level=level, identity=identity, source=source,
        non_interactive=non_interactive, tenant_id=tenant_id, version=version)
    if guard is GUARD_PASS:
        return None                     # ⑤ SA 预授权命中 ⇒ **放行**（不是"继续挂单"）
    if guard is not GUARD_CONTINUE:
        return guard                    # ④/⑥ 的拒绝结果

    # ⑦ 交互路径：L1 可复用批准；L2/L3 单次有效
    return _tool_approval_outcome(func_name, args, reason, level=level,
                                 tenant_id=tenant_id, version=version,
                                 identity=identity, source=source)


class _DeclaredCapabilityMeta:
    """**声明式能力**的最小元数据视图（字段名与 `agent.lines.models.ToolMeta` 对齐）

    仅供 :func:`check_declared_capability` 使用：让"没有 YAML 条目的能力"也能走**同一套**
    确认裁决（:func:`_confirm_level_outcome` 只通过 `getattr` 读这几个字段，见 `_meta_field`）。
    刻意做成极小的对象：不需要 `ToolMeta` 的全部字段，也不去伪造一个 `ToolMeta`
    （伪造就等于假装这个能力有 YAML 声明，那是把"未登记"洗成"已登记"）。
    """

    __slots__ = ("name", "plane", "effect", "risk",
                 "effective_confirm_level", "tenant_id", "version")

    def __init__(self, *, name: str, plane: str, effect: str, risk: str,
                 effective_confirm_level: str, tenant_id: str = "default",
                 version: str = "") -> None:
        self.name = str(name or "")
        self.plane = str(plane or "")
        self.effect = str(effect or "")
        self.risk = str(risk or "")
        self.effective_confirm_level = str(effective_confirm_level or "")
        self.tenant_id = str(tenant_id or "")
        self.version = str(version or "")


def check_declared_capability(capability: str, *, plane: str, effect: str, risk: str,
                              args: Optional[Dict[str, Any]] = None,
                              session_source: Optional[str] = None,
                              tenant_id: str = "default", version: str = ""
                              ) -> Optional[Dict[str, Any]]:
    """**声明式能力**的确认裁决入口：``None`` = 放行；dict = 拒绝 / 待审批（A2/S3）

    【为什么需要第二个入口】:func:`check_tool_call` 的确认级只能来自
    `data/tool_definitions/*.yaml`（工具面的单一真相源）。而**技能脚本执行面**
    （`agent/skills_mgmt/executor.py` 的 `subprocess.run`）是 `effect=execute` 的能力，
    在那个目录里**没有条目** ⇒ 走 `check_tool_call` 会被第 3 步判成"未登记 ⇒ 放行"
    （`_confirm_level_of` 返回空级别），等于把一个执行面留在治理网之外（审计 S3 / Q1 §5.3）。
    本函数让调用方**声明**三轴（plane/effect/risk），级别仍由本模块按
    `agent.lines.models.derive_confirm_level` 的**唯一派生口径**算出来 —— 调用方
    **不能**自己指定级别（否则就是"调用方给自己定级"，与 D1 的单一真相源冲突）。

    【fail-closed 的边界（三处，都朝"从严"一侧）】
      · 三轴任一为空 / `effect` 不在值域内 ⇒ 按最严处置（见下）；
      · 派生口径本身不可用（`agent.lines.models` 导入失败）⇒ 按 **L3**；
      · 总开关 `CP_TOOL_GATE_ENABLED=0` ⇒ 整体放行（**与** :func:`check_tool_call` 一致：
        一个总开关必须同时管住两个入口，否则第二个入口就是总开关的后门）。

    Args:
        capability: 能力标识（约定 `skill.<skill_id>`，也可用任何稳定 id）
        plane / effect / risk: 该能力的治理三轴（**必须是派生出来的事实，不是猜的**）
        args: 本次调用的参数（进审批单，供人工核对"这次要执行什么"）
        session_source: 同 :func:`check_tool_call`（None ⇒ 上下文/环境/``cli``）

    Returns:
        同 :func:`check_tool_call`：``None`` 或 ``{"ok": False, "blocked": True, ...}``
    """
    if _disabled_by_env():
        return None
    name = str(capability or "").strip()
    if not name:
        return None                                  # 无名调用：交回调用方报错

    plane_v = str(plane or "").strip().lower()
    effect_v = str(effect or "").strip().lower()
    risk_v = str(risk or "").strip().lower()
    try:
        from agent.lines.models import EFFECTS, derive_confirm_level  # noqa: PLC0415 惰性
        if effect_v not in EFFECTS:                  # 声明不合法 ⇒ 按最严（extend ⇒ L3）
            _warn_once("declared-effect:" + name,
                       "声明式能力 %s 的 effect=%r 不在值域 %s 内 ⇒ 按最严（extend）处置",
                       name, effect, tuple(EFFECTS))
            effect_v = "extend"
        if not plane_v:                              # plane 缺失 ⇒ 按治理平面（最严）
            _warn_once("declared-plane:" + name,
                       "声明式能力 %s 未声明 plane ⇒ 按最严（govern）处置", name)
            plane_v = "govern"
        level = derive_confirm_level(plane_v, effect_v, risk_v)
    except Exception as e:  # noqa: BLE001  派生口径不可用 ⇒ 证不出级别 ⇒ 拒（L3）
        logger.error("[tool_gate] 声明式能力 %s 的级别派生失败 ⇒ 按 L3 处置: %s: %s",
                     name, type(e).__name__, e)
        plane_v = plane_v or "govern"
        effect_v = effect_v or "extend"
        level = "L3"

    meta = _DeclaredCapabilityMeta(
        name=name, plane=plane_v, effect=effect_v, risk=risk_v,
        effective_confirm_level=level, tenant_id=tenant_id, version=version)
    outcome = _confirm_level_outcome(name, args, session_source, meta_override=meta)
    if isinstance(outcome, dict) and "confirm_level" not in outcome:
        # 【为什么补这个键】待审批路径（`_deny_approval`）的结果里**没有**确认级字段，
        #   而本入口的调用方（技能脚本执行面）需要把「拦在哪一级」机器可读地带回去
        #   （日志/审计/UI 提示都靠它）。这不是第二份口径：值就是上面刚派生出的那个 level。
        outcome = dict(outcome)
        outcome["confirm_level"] = level
    return outcome


def _noninteractive_guard(func_name: str, args: Optional[Dict[str, Any]],
                          reason: str, *, level: str, identity: str, source: str,
                          non_interactive: bool, tenant_id: str = "",
                          version: str = "") -> Any:
    """非交互 / 身份缺失 / SA 预授权三条判定的**共用实现**

    Returns:
        ``GUARD_CONTINUE`` = 本层未拦（继续走审批闭环）；
        ``GUARD_PASS``     = 本层判定**放行**（SA 预授权命中）；
        ``dict``           = 拒绝结果（且**不挂单**）。

    【🔴 2026-09-20 实测修复：原来只用 `None` 表达，导致 SA 预授权形同虚设】
      原实现的 SA 分支从 `_noninteractive_guard` 返回 `None`，而**同一个 `None`**
      在调用方（`_confirm_level_outcome` 与 `check_tool_call` 的兜底分支）被解读成
      "本层未拦 ⇒ 继续走 `_tool_approval_outcome`" ⇒ 于是 SA 的调用**照样挂单被拒**。
      更坏的是它**先落了 `decision=preauthorized` 的审计** ⇒ 审计说"以预授权执行"、
      实际结果却是 `APPROVAL_REQUIRED`（审计与事实相反，比没有审计更坏）。
      实测复现：`scope` 覆盖 `write_file` 且 `max_confirm_level=L2` 的 SA，
      `check_tool_call("write_file")` 返回 `APPROVAL_REQUIRED`。
      ⇒ 三种结局必须有**三个可区分的返回值**，不能压进一个 `None`。
      由 `tests/unit/test_confirm_level.py::TestServiceAccountPreauthorization` 锁定。

    【为什么要抽出来共用】确认分级边界（第 3 步）与 HITL/伦理兜底边界（第 4 步，
    针对**未登记**工具）都需要同一套身份判定。若只在第 3 步实现，第 4 步就成了
    "另一条能挂空单的路"—— 那正是 TASK-06 §3 第 3 步第 1 项要消除的缺陷。
    """
    # ④ 身份不可知 + 非交互 ⇒ 拒绝（**不挂单**：没人能批准，挂单就是悬空待办）
    if not identity and non_interactive:
        _audit_confirm_decision(
            tool=func_name, level=level, decision="denied_no_identity",
            identity="", source=source, reason=reason,
            tenant_id=tenant_id, version=version)
        return _deny_confirm(
            func_name,
            "%s；本次调用的**身份不可知**且来源 %r 表明无人在场 ⇒ 无法完成人工确认，"
            "已**直接拒绝**（未挂单，避免悬空待办）" % (reason, source or "?"),
            level=level, guidance=_NON_INTERACTIVE_GUIDANCE)

    # ⑤ SA 预授权（**闸门内的一条判定，不是旁路**）
    if identity == "service_account" and _preauthorized(func_name, identity, args, level):
        logger.info("[tool_gate] 工具 %s 以 **SA 预授权** 执行（%s，身份 %s）",
                    func_name, level, identity)
        _audit_confirm_decision(
            tool=func_name, level=level, decision="preauthorized",
            identity=identity, source=source,
            reason="以 SA 预授权执行（scope 覆盖且允许的最高级别 >= %s）" % level,
            tenant_id=tenant_id, version=version)
        return GUARD_PASS

    # ⑥ 非交互且无预授权 ⇒ 明确拒绝，不挂单（E5）
    if non_interactive:
        _audit_confirm_decision(
            tool=func_name, level=level, decision="denied_non_interactive",
            identity=identity, source=source, reason=reason,
            tenant_id=tenant_id, version=version)
        return _deny_confirm(
            func_name,
            "%s；本次调用的身份 %r / 来源 %r 属**非交互**（cron/CI/Webhook/后台），"
            "审批边界要求人工确认 ⇒ 已**直接拒绝**（未挂单：非交互来源下收件箱"
            "不会出现可裁决的待办，挂单只会得到一张永远等不到的单）"
            % (reason, identity or _IDENTITY_UNKNOWN, source or "?"),
            level=level, guidance=_NON_INTERACTIVE_GUIDANCE)
    return GUARD_CONTINUE


#: `_noninteractive_guard` 的三个可区分结局（**不能压进一个 `None`**，见其 docstring）
GUARD_CONTINUE = "continue"    #: 本层未拦 ⇒ 调用方继续走审批闭环
GUARD_PASS = "pass"            #: 本层判定**放行**（SA 预授权）⇒ 调用方直接 `return None`


#: 身份未声明时的占位显示（审计与错误文案统一用同一串，避免两处措辞漂移）
_IDENTITY_UNKNOWN = "<未声明>"
_NON_INTERACTIVE_GUIDANCE = (
    "出路（三选一）：① 改由**人工身份**执行一次（审批控制台 " + APPROVAL_CONSOLE_URL +
    " ，或 CLI \"pwsh -File scripts/approve_tool_call.ps1\"）；"
    "② 为该能力配置 **service_account 预授权**（SA token 的 scope 声明允许的能力集合"
    "与最高 confirm_level，v1.4 §10.2），之后以 SA 身份重试；"
    "③ 若该动作确实应长期免确认，请显式调低 `plane`/`effect`/`risk` 或声明"
    "`confirm_level` + `confirm_level_reason`（禁止静默降级）。"
)


def _deny_confirm(func_name: str, reason: str, *, level: str,
                  guidance: str = "") -> Dict[str, Any]:
    """确认分级的**硬拒绝**结果（区别于"待审批"：这里没有单号，重试也不会变）"""
    message = ("工具 %s 被集中式工具闸门拒绝（确认分级 %s）: %s"
               % (func_name, level, reason))
    logger.warning("[tool_gate] %s", message)
    result = {
        "ok": False,
        "blocked": True,
        "error_code": ERROR_CODE_PERMISSION_DENIED,
        "error": message,
        "tool": func_name,
        "reason": reason,
        "confirm_level": str(level or ""),
    }
    if guidance:
        result["guidance"] = guidance
    return result


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
# HITL 兜底判据 + 伦理硬规则（2026-09-18 接线）
# ─────────────────────────────────────────────────────────────


def _meta_for(func_name: str):
    """按候选写法取该工具的元数据（``None`` = 未登记）"""
    metas = _tool_meta()
    raw = str(func_name or "").strip()
    if not metas or not raw:
        return None
    for key in (raw, raw.lower(), _last_segment(raw)):
        if key and key in metas:
            return metas[key]
    return None


def _tool_exists(func_name: str) -> bool:
    """该工具名是否**真的在注册表里**；问不到注册表时返回 ``False``（= 不进兜底审批）

    为什么必须区分"已注册但无元数据"与"工具根本不存在"：模型把工具名**拼错**时，
    正确反馈是 ``ToolError: 未知工具``（让它改名重试），而**不是**挂一张审批单让人去批一个
    不存在的工具——那既浪费人的注意力，批准后重试还会再撞一次 ToolError。

    为什么问不到注册表时返回 ``False``（而不是 fail-closed 的 True）：紧跟本闸门之后的
    注册表查找**本来就会**对不存在的名字抛 ``ToolError``，语义无损；而在注册表为空
    （未装配 / 单测环境）时把一切判成"存在"，会让**每一次**未知调用都去挂单 ——
    那是把"兜底安全网"退化成"噪声源"。fail-closed 的方向在这里由"是否有元数据"承担
    （见 :func:`_hitl_boundary`），不由"名字是否可验证"承担。
    """
    raw = str(func_name or "").strip()
    if not raw:
        return False
    try:
        # 惰性导入：agent.tools 会导入本模块 ⇒ 模块级导入会成环
        from agent import tools as _tools  # noqa: PLC0415
        names = {str(t.get("name") or "").strip()
                 for t in (_tools.list_tools() or []) if isinstance(t, dict)}
        names.discard("")                      # 空名不是名字（否则会误命中）
        if not names:
            return False                       # 注册表不可见 ⇒ 交回 ToolError 语义
        last = _last_segment(raw)
        return raw in names or bool(last and last in names)
    except Exception as e:  # noqa: BLE001 问不到注册表 ⇒ 同上（不制造审批噪声）
        logger.debug("[tool_gate] 工具存在性不可判定（按不存在处理）: %s: %s",
                     type(e).__name__, e)
        return False


def _hitl_boundary(func_name: str, args: Optional[Dict[str, Any]]) -> Optional[str]:
    """**只对"存在但未登记元数据"的工具**做 fail-closed 兜底；其余交回既有权威

    为什么需要这一步：本模块第 2/3 步只认**已登记元数据**，对
    ``data/tool_definitions/`` 里没有条目的工具（动态生成、外部接入）一律放行；而模块
    docstring 一直写着"真正的 fail-closed 由 ``HITLManager.assess`` 承担（未登记工具
    ⇒ HIGH）"—— 实测那一层**在生产里零调用方**（只有测试引用）。于是两处都不拦：
    **文档承诺的兜底根本不存在**。

    为什么**不**覆盖"工具不存在"的情形：那是拼错名字，正确反馈是 ``ToolError``
    （见 :func:`_tool_exists` 的 docstring），挂单只会让人白批一张单。

    为什么**不**把已登记工具的 HITL 结果也搬过来：``assess`` 对 ``risk: high`` 就返回
    HIGH，而"已登记工具的确认级别"由 YAML 的治理三轴**派生**（``risk: high`` ⇒ L2，
    见 :func:`agent.lines.models.derive_confirm_level`）。元数据的**唯一权威是 YAML**，
    这里只补 YAML 覆盖不到的那一块，绝不重复判定 —— 否则同一个事实会有两份口径
    （D1），且任何一处的阈值改动都会与另一处**静默打架**。
    【2026-09-20 措辞更新（TASK-06）】本段原写作"``write_file`` / ``edit`` / ``git`` /
    ``apply_patch`` 都是 high 且 ``needs_approval=False``（它们不该每次都要人确认）"。
    那句里的两个事实都已改变：① ``needs_approval`` 现在**含** ``high``；
    ② "不该每次都要人确认"的诉求改由**分级**满足（L1 可复用批准、L2 逐次确认），
    而不是靠"完全不拦"。结论（不越权重复判定）不变。

    失败语义：HITL 不可用 ⇒ 返回 ``None``（交回既有的 fail-open 口径），**不额外收紧**。
    """
    if _meta_for(func_name) is not None:
        return None                      # 已登记 ⇒ YAML 说了算，不越权
    if not _tool_exists(func_name):
        return None                      # 名字不存在 ⇒ 交回 ToolError，不挂单
    try:
        from agent.human_in_the_loop.hitl import HITLManager  # noqa: PLC0415 惰性
        risk = HITLManager().assess(str(func_name or "").strip(), args or {})
    except Exception as e:  # noqa: BLE001 审批权威不可用 ⇒ 不改变既有行为
        logger.debug("[tool_gate] HITL 判定不可用（按不拦处理）: %s: %s",
                     type(e).__name__, e)
        return None
    level = getattr(risk, "value", risk)
    level = str(level or "").strip().lower()
    if level in ("high", "critical"):
        return ("已注册但未登记元数据（data/tool_definitions/ 无条目）"
                "⇒ HITL fail-closed 判 %s" % level)
    return None


#: 伦理检查只作用于**会造成后果**的工具（effect ∈ 此集合）
_ETHICS_EFFECTS = ("execute", "extend")


def _ethics_boundary(func_name: str, args: Optional[Dict[str, Any]]) -> Optional[str]:
    """伦理硬规则命中 ⇒ 返回原因串（**升级为"需人工审批"，不直接判死**）

    【为什么要接】``agent/human_in_the_loop/ethics.py::EthicsEngine`` 自称"不可突破的
    硬约束"（禁 `rm -rf /`、禁格式化、禁关机、禁读 `/etc/passwd`、禁改 orchestrator、
    禁违法内容），实测**生产零调用方**（只有 8 处测试引用）⇒ 6 条硬规则一条都没生效。

    【为什么是"升级为审批"而不是"直接拒绝"】伦理规则天生带近似性（E002/E003 判的是
    "命令动词有没有出现在命令位上"），仍可能有误报：``echo "format C:"`` 这类
    字符串常量也会命中。硬拒会把误报变成"工作直接卡死"，而升级为审批只是多一次点击 ⇒
    规则真正生效、误报代价可控。若将来要把某几条做成**不可覆盖的硬拒**（例如含
    ``rm -rf /`` 的），只需在这里按 rule id 分流，调用方无需改动。

    【作用域】只查 ``effect ∈ {execute, extend}`` 的工具（**会造成后果**的调用），
    读类工具（``grep``/``read_file`` 等）不查 —— 避免"读一个含 shutdown 字样的日志
    也要审批"这类纯噪声。元数据缺失时同样检查（那正是最需要看住的场景）。

    【2026-09-22 匹配口径修复（待办台账 #2）】原实现把**整个参数字典**（含键名）
    交给 ``EthicsEngine`` 做裸子串匹配，于是 ``delegate`` 的必填参数
    ``artifact_format`` 让**每一次**委派都报 E002「禁止格式化磁盘」，理由与任务内容
    无关；``git log --format=%H`` / ``grep shutdown`` 同样误报。本层不再改动，
    修复落在 ``EthicsEngine`` 自身：**只看参数值**（键名不参与）+ 命令动词必须落在
    "命令位"。理由因此重新可信，误报也不再"独自"把回滚态/影子态下本应放行的调用拦下。
    """
    meta = _meta_for(func_name)
    if meta is not None and str(getattr(meta, "effect", "")) not in _ETHICS_EFFECTS:
        return None
    try:
        from agent.human_in_the_loop.ethics import EthicsEngine  # noqa: PLC0415 惰性
        violations = EthicsEngine().check(str(func_name or "").strip(), args or {})
    except Exception as e:  # noqa: BLE001 伦理引擎不可用 ⇒ 不改变既有行为
        logger.debug("[tool_gate] 伦理检查不可用（按不拦处理）: %s: %s",
                     type(e).__name__, e)
        return None
    if not violations:
        return None
    ids = ", ".join(f"{r.get('id')}（{r.get('desc')}）" for r in violations
                    if isinstance(r, dict))
    return "命中伦理硬规则：%s" % (ids or "（规则未提供 id）")


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
    """严格模式使用的角色（``CP_PERMISSION_DEFAULT_ROLE``；缺省/非法 → ``guest``）

    返回 ``agent.permission_system.Role`` 枚举成员。只接受合法枚举值；**非法值告警并
    回退 ``_DEFAULT_STRICT_ROLE``**（不抛异常）。``Role`` 导入失败向上抛，由
    :func:`_strict_deny_or_open` 的 fail-open 边界放行。

    【🔴 2026-09-20 措辞更正（TASK-06 E15）】本 docstring 原写作"缺省/非法 → owner"，
    并给了一条理由："回退比因为拼错一个环境变量就变成全量拒绝安全得多"。
    那个理由**正是 TASK-06 要修的缺陷本身**：它把"拼错一个环境变量"的后果定为
    **特权**（`owner` 的 `allowed_tools=["*"]` ⇒ 严格模式等于没开），而 TASK-06 §3
    第 6 步第 6 项明确要求"严格模式的缺省角色改为**最小权限**（拒绝或只读）"。
    安全开关的误配置必须落在**更严**的一侧，这与本模块其余各处的 fail-closed 取舍一致
    （"依据读不到 ⇒ 不放行"）。
    实现上**只有一条规则**：任何取不到合法值的情形都回退 `_DEFAULT_STRICT_ROLE`
    （现为 `guest`）——不为"未设置"与"非法"各留一套判据（D1）。
    要恢复旧的宽松口径：显式设 ``CP_PERMISSION_DEFAULT_ROLE=owner``（既有开关，无新变量）。
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
