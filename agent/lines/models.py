"""能力平面与主线 —— 数据模型

【为什么有这个包】
    云枢的工具装配此前只有一个**全局开关**（`data/tools_config.json` 的扁平
    `tool_states`），对"不同 Agent 各管一条线"这件事没有任何支持面
    （见 docs/工具集评估与重分类报告.md §4.7）。

    本包把它换成两层：
        L1  工具原子 → 由 `data/tool_definitions/*.yaml` 声明
                        plane / effect / risk / tags（唯一权威）
        L2  主线档案 → 由 `data/agent_lines/*.yaml` 声明
                        平面权重 + 核心工具 + 效果上限 + 技能包

【四平面】
    resident  常驻 —— 每轮必发（高频低 token）
    perceive  感知 —— 只读取，不改变世界
    act       行动 —— 改变世界
    govern    治理 —— **改变云枢自身能力集** ⇒ 天然就是审批边界

【三维正交】
    plane   决定"放在装配阶梯的哪一层"（组装用）
    effect  决定"最多能造成什么后果"（治理用：read<write<execute<extend）
    risk    决定"要不要人工确认"（审批用）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL_DEFS_DIR = os.path.join(_ROOT, "data", "tool_definitions")
AGENT_LINES_DIR = os.path.join(_ROOT, "data", "agent_lines")

PLANES = ("resident", "perceive", "act", "govern")
EFFECTS = ("read", "write", "execute", "extend")
RISKS = ("low", "medium", "high", "critical")

#: 「可被 LLM 调用」声明的取值域（唯一来源：agent/lines/callability.py，此处只做解析）
#: 为什么在这里再列一次而不 import：`callability` 反过来要 import 本模块的目录常量，
#: 互相 import 会形成环；两处取值域由 tests/unit/test_tool_callability.py 对拍锁死。
TOOL_TYPES = ("tool", "skill", "api", "script")
CALLABLE_MODES = ("auto", "required", "manual")
PERMISSION_LEVELS = ("public", "internal", "restricted")

#: 工具侧**确认分级**四级（v1.4 §10.2；TASK-06 新增）
#: 【为什么新建而不是复用技能域的 L0/L1/L2】`agent/skills_mgmt/approval.py:71` 的
#: `APPROVAL_LEVELS=("L0","L1","L2")` 是**技能/策略审批**域的口径（L2=manual_required），
#: 与"一次工具调用要不要人点确认"不是同一件事（值域都少一级）。两域各自独立，
#: 不互相 import —— 硬凑成一个常量会让某一域将来改级时被迫带上另一域的语义。
CONFIRM_LEVELS = ("L0", "L1", "L2", "L3")

#: 四级语义（人读；审计与盘点表引用同一份文案，避免两处措辞漂移）
CONFIRM_LEVEL_SEMANTICS = {
    "L0": "免确认（只读且低风险）",
    "L1": "摘要确认（展示将执行什么 + 影响范围，**可批量确认**）",
    "L2": "逐次确认（每次都要人点，且单次有效）",
    "L3": "默认禁止（仅**显式预授权** —— SA + scope —— 才能执行）",
}

# ── CapabilitySpec 新增维度（v1.4 §5.1；TASK-04）─────────────────────────
#: 能力形态归并（见 docs/rfc/CapabilitySpec规范.md §2）：
#:   本文件是**工具侧**的模型，值域里保留 `skill` 只为让历史数据可解析；
#:   `api` → `kind=tool` + `location=remote`（远端接口），`script` → `kind=skill`（脚本型技能）。
#:   **技能侧的 kind 不来自 data/tool_definitions/*.yaml**，只来自
#:   `data/skill_callability.yaml` 的 defaults + 覆盖（见 callability.load_skill_declarations）。
KINDS = ("tool", "skill")

#: 执行位置：`local` = 同进程内执行；`remote` = 跨进程/协议边界（含本机 stdio 子进程）
#: 为什么在这里再列一次而不 import agent/lines/location.py：与上面 TOOL_TYPES 同一理由
#: （避免新增依赖边），一致性由 tests/unit/test_capability_spec.py 对拍锁死。
LOCATIONS = ("local", "remote")

#: 能力归属：谁把它装进来的（仓库里此前**没有**这个概念）
#:   builtin           随仓库发布（data/tool_definitions/*.yaml + 内置注册点）
#:   local-installed   本机安装的扩展/MCP 服务（data/extensions、mcp 连接）
#:   tenant-installed  租户安装（预留；当前恒为空 —— 单机单用户，见 §6）
#:   marketplace       来自扩展市场（SOURCE_MARKET）
OWNERS = ("builtin", "local-installed", "tenant-installed", "marketplace")

#: 执行隔离级别（TASK-07 §4 第 5 项 / E8）：`none` | `process` | `container`
#: 【本仓的诚实取值是 `process`】进程级隔离 = 子进程 + 超时 kill + 输出截断 +
#: 命令校验 + 路径白名单（`agent/subagent/sandbox.py`）。**没有**容器/WASM：
#: `Sandbox.get_docker_sandbox()` / `get_wasm_sandbox()` 都返回 `None`。
#: 【为什么要有这个字段】没有它时，评审只能从 `sandbox_allowed` 猜"沙箱有多强"，
#: 容易误以为存在容器隔离。本字段把"没有容器隔离"这件事**放进能力清单**。
#: 【D1】运行期事实来源是 `agent/subagent/sandbox.py::ISOLATION_LEVEL`；
#: 本常量与它的一致性由 `tests/unit/test_sandbox_wiring.py::TestIsolationHonesty` 锁死。
ISOLATION_LEVELS = ("none", "process", "container")

#: 本仓当前的隔离级别（改动此值前请先读 `agent/subagent/sandbox.py` 的适配位实现）
DEFAULT_ISOLATION_LEVEL = "process"

#: `tenant_id` 的占位默认。单机单用户下**恒为 default**，来源是**派生**（服务端），
#: 不是请求参数 —— 见 `agent/routes_ui_panels.py` 的客户端传参矫正（TASK-04 §6.3）。
DEFAULT_TENANT_ID = "default"

#: 命名空间（`capability_id` 的第二段）。当前全部工具都在同一命名空间下，
#: 值取自 `namespace` 键，缺省用 `yunshu`（仓库名）。
DEFAULT_NAMESPACE = "yunshu"

#: effect 的偏序：用于 policy.effect_allow 的包含判定
_EFFECT_ORDER = {"read": 0, "write": 1, "execute": 2, "extend": 3}

#: 风险的推荐审批阈值：risk >= 此值则默认需要人工确认
#
# 【🔴 TASK-06 核心一行改动（本任务最高风险的一行）】原值为 `{"critical"}`。
#   实测后果：91 个 YAML 里 `risk: high` 有 **13 个**（apply_patch / connect_mcp /
#   decompress / edit / ext_install / ext_send_channel / ext_uninstall / fan_out /
#   git / run_program / schedule_task / workspace_delete / write_file），
#   它们**完全不触发人工确认** —— 那是"写文件/改文件/删工作区/装扩展"这一类动作。
#   `agent/tool_gate.py:769-773` 当初注明"不把已登记工具的 HITL 结果搬过来"的动机
#   是"否则所有写操作都要点确认"。加 `high` 后那个顾虑由**分级**解决（见
#   `derive_confirm_level`）：L0/L1 仍不逐次确认，只有 13 个 high 进 L2。
#
# 【回滚】本行 + `confirm_level` 派生由 `CP_TOOL_CONFIRM_LEVEL_ENFORCE` 门控
#   （默认开启，置 0 退回"只有 critical 挂单"的旧行为）。见 `agent/tool_gate.py`。
_APPROVAL_FROM_RISK = {"critical", "high"}


def derive_confirm_level(plane: str, effect: str, risk: str) -> str:
    """由治理三轴**派生** `confirm_level`（唯一派生口径；不是人工填的字段）

    规则（TASK-06 §3 第 2 步的表；**顺序敏感，从严者先判**）：

    | 条件 | 级别 | 语义 |
    |---|---|---|
    | `risk: critical` \\| `effect: extend` \\| `plane: govern` | **L3** | 默认禁止（须显式预授权） |
    | `risk: high` | **L2** | 逐次确认（本任务要修的核心缺陷，13 个工具） |
    | `effect: write` \\| `effect: execute` \\| `risk: medium` | **L1** | 摘要确认（可批量） |
    | 其余（即 `effect: read` 且 `risk: low`） | **L0** | 免确认 |

    【为什么 ① 必须排在 ② 之前】`shell_execute` 是 `risk: critical` + `plane: act`、
    `generate_tool` 是 `critical` + `govern`。若先判 `risk`，两条都会落进 L2
    （"点一下就行"）—— 而"改变云枢自身能力集"与"不可逆高危"要求的是**默认禁止**。
    同理：13 个 `risk: high` 里有 3 个（`connect_mcp` / `ext_install` /
    `ext_uninstall`）同时是 `plane: govern` ⇒ 它们按 **L3** 处置（更严），
    而不是按 L2。**13 个一个都没漏**，只是其中 3 个被抬到 L3。

    【🔴 实测补的一处口径空洞（TASK-06 原表没有这一行）】
    TASK-06 §3 第 2 步的表只列了 `read+low` / `write 或 medium` / `high` / `critical…
    extend…govern` 四行。而仓库里存在**第 5 种组合**：`effect: execute` + `risk: low`
    —— 实测 **2 个工具**（`notify` / `run_lint`）正是这一组合。
    若把它归入 L0（"免确认"），就等于**免确认地执行程序**，与仓库自己的
    `effective_permission_level` 口径直接冲突：那条口径对它们给的是 `internal`
    而不是 `public`（`execute` 不是只读）。故本实现把 `execute` 与 `write` 同等对待
    → **L1**。修正后 `L0 ⟺ read ∧ low`，与 `public` 的判定逐条等价（见下）。

    【与 `permission_level` 的一致性（D1：不允许两套并行口径）】
    在 `_APPROVAL_FROM_RISK` 含 `high` 之后，两条派生**逐条等价**：

        `confirm_level == "L0"`  ⟺  `permission_level == "public"`（且未被策略拒绝）

    证明：L0 ⟺ (read ∧ low ∧ ¬govern ∧ ¬extend)；
          public ⟺ (¬needs_approval ∧ ¬denied ∧ read ∧ low)，
          而 `needs_approval` = govern ∨ extend ∨ risk∈{critical,high}
    ⇒ 两者是**同一组条件**的两种写法。由 `tests/unit/test_confirm_level.py`
      在**全量 114 条**上对拍锁死（E8）。唯一允许的例外是 `denied=True`
      （策略拒绝 ⇒ permission_level 降为 restricted，而 confirm_level 反映的是
      "本来该几级确认"）—— 该例外在测试里逐条列举理由，不静默放过。
    """
    p = str(plane or "").strip().lower()
    e = str(effect or "").strip().lower()
    r = str(risk or "").strip().lower()
    if r == "critical" or e == "extend" or p == "govern":
        return "L3"
    if r == "high":
        return "L2"
    if e in ("write", "execute") or r == "medium":
        return "L1"
    return "L0"


def needs_approval_for(plane: str, effect: str, risk: str) -> bool:
    """`needs_approval` 的**唯一权威判定**（`ToolMeta` 与 `callability` 共用）

    【为什么必须抽成函数】改 `_APPROVAL_FROM_RISK` 之前，这条规则在仓库里有**三份
    手写副本**：`agent/lines/models.py::ToolMeta.needs_approval`、
    `agent/lines/callability.py::_tool_entry`（第 751 行）、
    `tests/unit/test_tool_callability.py`（第 97-98 行，`risk == "critical"`）。
    TASK-06 要把阈值从 `critical` 提到 `high` ⇒ 三份副本必须**同时**改，
    漏一处就是"改了却测不出来"（守卫测试用的还是旧口径，绿灯掩盖缺口）。
    抽成函数后三处共用同一实现，D1 的"单一真相源"才真的成立。
    """
    return (str(plane or "").strip().lower() == "govern"
            or str(effect or "").strip().lower() == "extend"
            or str(risk or "").strip().lower() in _APPROVAL_FROM_RISK)


def confirm_level_rank(level: str) -> int:
    """确认级别 → 序号（`L0`=0 … `L3`=3）；未知值 → **-1**（调用方据此从严处置）

    Why 未知返回 -1 而不是 0：`-1` 会让"未知级别 >= L2 吗"这类比较落到 False，
    看起来像"放行"—— 所以调用方**不得**直接用它做放行判定，必须先判 `>= 0`。
    本函数只服务"比较严宽"，放行判定一律用 `CONFIRM_LEVELS` 成员测试。
    """
    try:
        return CONFIRM_LEVELS.index(str(level or "").strip().upper())
    except ValueError:
        return -1


@dataclass(frozen=True)
class ToolMeta:
    """单个工具的能力元数据（来自 data/tool_definitions/<name>.yaml）"""

    name: str
    category: str = ""
    plane: str = "act"
    effect: str = "execute"
    risk: str = "medium"
    tags: tuple = ()
    description: str = ""
    #: 内部工具：保留注册（供 AsyncExecutor 等按名调用），但不进模型可见集。
    #: 为什么需要它：`call()` 要求名字在 `_registry` 中，所以"内部执行体"不能注销，
    #: 只能从 `get_tool_defs()` 里隐藏。
    internal: bool = False

    # ── 「可被 LLM 调用」声明（见 agent/lines/callability.py）──
    #: 能力形态：tool | skill | api | script
    tool_type: str = "tool"
    #: 声明：是否允许 LLM 发起调用（**生效值**还要过 schema/执行器/权限三关）
    llm_callable: bool = True
    #: auto（模型自主判断）| required（必须调用）| manual（仅人工/系统）
    callable_mode: str = "auto"
    #: public | internal | restricted（与 plane/effect/risk 的派生值必须一致）
    permission_level: str = ""
    #: 是否允许在沙箱（受限会话，默认只读）中执行
    sandbox_allowed: bool = True
    #: 执行隔离级别：none | process | container（TASK-07 新增；默认取本仓**诚实**值）
    #: 【与 `sandbox_allowed` 的区别】`sandbox_allowed` 回答"**允不允许**在受限会话里
    #: 跑"；本字段回答"跑起来时**有多强的隔离**"。两者正交：一个能力可以
    #: `sandbox_allowed=true`（允许在沙箱跑）而 `isolation_level=process`（只有进程级）。
    #: 旧 YAML 没有这个字段 ⇒ 取默认值 `process`（D2：新增字段可选、有默认值）。
    isolation_level: str = DEFAULT_ISOLATION_LEVEL
    #: 不可调用原因（llm_callable=false 时必填）
    reason: str = ""

    # ── CapabilitySpec 扩展（v1.4 §5.1；TASK-04 新增）────────────────────
    # 【D2 向后兼容】以下字段**全部可选且有默认值**，`to_dict()` 只增不减；
    # 既有消费者（agent/lines、agent/hitl、tool_gate、rate_limiter、subagent/toolset）
    # 不受影响。字段语义与判定规则见 docs/rfc/CapabilitySpec规范.md。
    #: 执行位置：local | remote。**默认 remote（保守侧）**：
    #:   未登记/未识别的能力按"会跨出进程边界"对待 —— 那是更受约束的一侧
    #:   （需要超时、熔断、SSRF 检查、网络审计）；漏判的代价是安全缺口，
    #:   误判的代价只是一条多余的约束。与本文件 `load_tool_meta` 的
    #:   "缺字段给保守默认（act/execute/medium）"同一取舍。
    location: str = "remote"
    #: 声明 `location` 的理由（作为**钉住值**时必须写明；缺省为空 ⇒ 判定器接管）
    location_reason: str = ""
    #: 归属：builtin | local-installed | tenant-installed | marketplace
    owner: str = "builtin"
    #: 能力版本（YAML 的 `version`；仓库现状 91/91 都有，但治理未启动）
    version: str = "1.0.0"
    #: 命名空间（`capability_id` 第二段）
    namespace: str = DEFAULT_NAMESPACE
    #: 租户占位（当前恒为 default；来源是服务端派生，不是请求参数）
    tenant_id: str = DEFAULT_TENANT_ID
    #: 注册表来源：global（`agent/tools/_registry`）| planning（`planning.ToolRegistry`）
    #: 【为什么要有】`get_status` / `search_memory` / `get_sensor_summary` **同名两表**，
    #:   不标出来就分不清"这个名字指的是哪一个"（见 §2.7 的静默改名机制）。
    registry_source: str = "global"
    #: 输入契约（**别名**：YAML 的 `schema:` 键就是它 —— 做别名，不改名）
    input_schema: Optional[Dict[str, Any]] = None
    #: 输出契约 / 结果契约（`output_schema` 与 `result_schema` 是同一件的两种叫法）
    output_schema: Optional[Dict[str, Any]] = None
    #: CapabilitySpec 自身版本（首期恒为 1；v1.4 §1.3 自述"首期不强制签名与 SBOM"）
    manifest_version: int = 1
    #: 预留（v1.4 §1.3：首期不强制，但预留接口）—— 当前**无任何消费者**
    signature: str = ""
    source_trust: str = ""
    compatibility: str = ""
    semver_policy: str = ""
    #: 健康态（`agent/health/` 的产物；TASK-05 才接线，当前一律留空）
    health: str = ""
    #: 是否已废弃（证据：YAML 的 `deprecated`；当前 91/91 全 false ⇒ 淘汰机制未启动）
    deprecated: bool = False
    #: 显式别名（同名冲突时 `register_dynamic` 自动加的 `_2`/`_3` 名字登记在此，
    #: 使"静默改名"变成**可见的别名**，见 TASK-04 §3 第 2 步第 5 项）
    aliases: tuple = ()

    # ── 工具侧确认分级（v1.4 §10.2；TASK-06 新增）────────────────────────
    # 【D2 向后兼容】YAML 里 `confirm_level` **一个都没写**（实测 91/91 均无），
    # 空串 ⇒ 完全由 :func:`derive_confirm_level` 派生 ⇒ 既有 YAML 零改动即可工作。
    #: 显式声明的确认级别（`L0`–`L3`）；空串 = 未声明 ⇒ 走派生
    confirm_level: str = ""
    #: 声明理由。**降级（比派生值更宽）时必须给**，否则该 override 被忽略
    #: （见 :func:`_resolve_confirm_level`）。升级（比派生值更严）不需要理由。
    confirm_level_reason: str = ""

    @property
    def kind(self) -> str:
        """能力形态（归并后）：`api`→`tool`、`script`→`skill`；`tool`/`skill` 为**恒等映射**。

        【不易·2026-09-20 修一处实现与文档/测试不一致】
        原实现只判断 `tool_type == "script"`，其余**一律返回 `"tool"`** ——
        于是 `tool_type="skill"` 被错判成 `kind="tool"`，与
        `tests/unit/test_capability_spec.py::test_kind_归并规则` 的断言直接冲突
        （那 4 条断言：`api`→`tool`、`script`→`skill`、`tool`→`tool`、`skill`→`skill`）。

        Why 取"恒等映射"而不是"改测试"：
          `kind` 的定义就是**把 4 值域归并成 2 值域**（见 `KINDS` 与
          `docs/rfc/CapabilitySpec规范.md` §2）。归并规则只对 `api` / `script`
          这两个"非形态"取值做映射；`tool` 与 `skill` **本身就是形态**
          ⇒ 它们应当是恒等映射。若把 `skill` 也塌成 `tool`，则 `kind` 会丢失
          "这条能力是技能形态"的信息，与 `KINDS` 里保留 `skill` 相矛盾。

        影响面（实测确认无副作用）：91 个 `data/tool_definitions/*.yaml` 的
        `tool_type` **全部是 `tool`** ⇒ 本属性对全部现存工具都返回 `"tool"`，行为不变；
        技能侧的 23 条**不经过 `ToolMeta`**（走 `data/skill_callability.yaml`）。
        生产代码里 `kind` 的唯一消费点是 `to_dict()`（`:210`），未做 `== "skill"` 比较。
        """
        if self.tool_type in ("script", "skill"):
            return "skill"
        return "tool"

    @property
    def capability_id(self) -> str:
        """全局唯一能力标识：`tenant_id:namespace:name@version`

        【为什么用这个顺序】v1.4 §5.1 要求 Registry/Router/缓存/审计/配额键**都带
        `tenant_id`**（当前恒为 `default`，但键形状必须现在就正确，否则将来一开多租户
        就是全量键重写）。`tool_name` **保留为别名/短键**（D2：agent/lines 与 UI 都在用它）。
        """
        return f"{self.tenant_id}:{self.namespace}:{self.name}@{self.version}"

    @property
    def schema(self) -> Optional[Dict[str, Any]]:
        """`input_schema` 的**旧名别名**（YAML 用的是 `schema:`，勿改名）"""
        return self.input_schema

    @property
    def result_schema(self) -> Optional[Dict[str, Any]]:
        """`output_schema` 的别名（v1.4 §5.1 里两个名字都出现过，统一指向同一份契约）"""
        return self.output_schema

    @property
    def needs_approval(self) -> bool:
        """治理平面 / 改变能力集 / 高危 ⇒ 需要人工确认

        【TASK-06】本属性随 `_APPROVAL_FROM_RISK` 纳入 `high` 而**首次为真**地为
        13 个写类工具（write_file / edit / git / apply_patch / run_program …）。
        它与 `effective_confirm_level >= "L2"` 是同一事实的两种表达（见
        `derive_confirm_level` 的一致性证明），由 `tests/unit/test_confirm_level.py`
        在 114 条上对拍。
        """
        return needs_approval_for(self.plane, self.effect, self.risk)

    @property
    def derived_confirm_level(self) -> str:
        """**纯派生**的确认级别（忽略 YAML override；对拍与守门测试用）"""
        return derive_confirm_level(self.plane, self.effect, self.risk)

    @property
    def effective_confirm_level(self) -> str:
        """**生效**的确认级别：YAML 显式声明优先，否则派生

        为什么"声明优先"而不总是以派生为准：TASK-06 §3 第 2 步第 4 项允许业务上
        确实高频的工具显式声明（例如某个 `high` 工具），但**必须记理由**
        （`confirm_level_reason`）且**禁止静默降级** —— 理由缺失时的降级声明
        在 `load_tool_meta()` 里就已被丢弃（回落派生值），故此处无须再判。
        """
        declared = str(self.confirm_level or "").strip().upper()
        if declared in CONFIRM_LEVELS:
            return declared
        return self.derived_confirm_level

    @property
    def confirm_level_overridden(self) -> bool:
        """是否被 YAML 显式覆盖（盘点表据此回答"这个级别是算出来的还是人填的"）"""
        return str(self.confirm_level or "").strip().upper() in CONFIRM_LEVELS

    @property
    def confirm_level_semantics(self) -> str:
        """该级别的中文语义（与 `CONFIRM_LEVEL_SEMANTICS` 同一份文案）"""
        return CONFIRM_LEVEL_SEMANTICS.get(self.effective_confirm_level, "")

    @property
    def requires_preauthorization(self) -> bool:
        """是否 L3 —— **默认禁止**，只有显式预授权（SA + scope）才能执行

        Why 单列一个属性：`agent/tool_gate.py` 对 L3 的处置与 L2 **不同**
        （L2 = 挂单等人工裁决；L3 = 直接拒绝，人工裁决也不行），
        而调用点若自己写 `== "L3"` 就会与派生口径脱钩。
        """
        return self.effective_confirm_level == "L3"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "plane": self.plane,
            "effect": self.effect,
            "risk": self.risk,
            "tags": list(self.tags),
            "needs_approval": self.needs_approval,
            "internal": bool(self.internal),
            "tool_type": self.tool_type,
            "llm_callable": bool(self.llm_callable),
            "callable_mode": self.callable_mode,
            "permission_level": self.permission_level,
            "sandbox_allowed": bool(self.sandbox_allowed),
            "isolation_level": str(self.isolation_level or DEFAULT_ISOLATION_LEVEL),
            "reason": self.reason,
            # ── CapabilitySpec 扩展（只增不减，D2）──
            "kind": self.kind,
            "location": self.location,
            "location_reason": self.location_reason,
            "owner": self.owner,
            "version": self.version,
            "namespace": self.namespace,
            "tenant_id": self.tenant_id,
            "capability_id": self.capability_id,
            "registry_source": self.registry_source,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "manifest_version": self.manifest_version,
            "signature": self.signature,
            "source_trust": self.source_trust,
            "compatibility": self.compatibility,
            "semver_policy": self.semver_policy,
            "health": self.health,
            "deprecated": bool(self.deprecated),
            "aliases": list(self.aliases),
            # ── 工具侧确认分级（TASK-06 新增；只增不减，D2）──
            # 同时输出"生效值"与"派生值"：盘点表要能回答"这条级别是人填的还是算出来的"
            "confirm_level": self.effective_confirm_level,
            "confirm_level_declared": self.confirm_level,
            "confirm_level_derived": self.derived_confirm_level,
            "confirm_level_reason": self.confirm_level_reason,
            "confirm_level_overridden": self.confirm_level_overridden,
            "confirm_level_semantics": self.confirm_level_semantics,
            "requires_preauthorization": self.requires_preauthorization,
        }


def _norm(value: Any, allowed: tuple, default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _confirm_level_of(value: Any) -> str:
    """解析 YAML 的 `confirm_level`（大小写不敏感：`L2` / `l2` 都接受）

    Why 单独一个助手而不复用 `_norm`：`_norm` 会 `lower()` 后比对，而
    `CONFIRM_LEVELS` 是**大写**字母 + 数字（`L0`…`L3`）。若为了让 `_norm` 能用
    而把常量改成小写，盘点表与审计里就会出现 `l2` 这种与文档不一致的写法
    （D1：口径只有一套）。
    """
    text = str(value or "").strip().upper()
    return text if text in CONFIRM_LEVELS else ""


def _resolve_confirm_level(declared: str, derived: str,
                           reason: Any) -> tuple:
    """裁定 YAML 的 `confirm_level` override —— 返回 `(生效声明值, 说明)`

    【不对称规则，这是刻意的】
      · 声明值 **更严**（级别序号 > 派生值）⇒ **直接接受**，不需要理由：
        "把保护调高"永远安全；要求理由只会让人为了省事而不去调高。
      · 声明值 **更宽**（级别序号 < 派生值）⇒ **必须有 `confirm_level_reason`**；
        理由缺失 ⇒ **丢弃该 override**（回落派生值）并把原因写进说明。
        这正是 TASK-06 §3 第 2 步第 4 项"**禁止静默降级**（v1.4 ADR-028 精神）"
        的落地方式：降级要么写在明面上（有理由、可查询），要么不生效 ——
        不存在"悄悄降了一级而盘点表看不出来"的第三种状态。
      · 声明值 == 派生值 ⇒ 视为冗余声明、按未覆盖处理（避免盘点表虚报 override）。

    Returns:
        `(declared_or_empty, note)`；`declared_or_empty` 为空串表示"按派生"。
    """
    if not declared:
        return "", ""
    if declared == derived:
        return "", f"YAML 声明 {declared} 与派生值相同 ⇒ 视为未覆盖（冗余声明）"
    note = str(reason or "").strip()
    d_rank, y_rank = confirm_level_rank(declared), confirm_level_rank(derived)
    if y_rank >= 0 and d_rank > y_rank:
        return declared, (f"YAML 显式**收紧**至 {declared}（派生值 {derived}）"
                          + (f"；理由：{note}" if note else ""))
    if not note:
        # 降级且无理由 ⇒ 丢弃（fail-closed：宁可多确认一次，不可静默放宽）
        return "", (f"YAML 声明 {declared} 比派生值 {derived} **更宽**且未给 "
                    "confirm_level_reason ⇒ 该 override 被丢弃（禁止静默降级）")
    return declared, f"YAML 显式**放宽**至 {declared}（派生值 {derived}）；理由：{note}"


def _as_bool(value: Any, default: bool) -> bool:
    """把 YAML 的布尔写法收敛成 bool（`true/false/1/0/yes/no/on/off`）

    【不易·名字有讲究，勿改回 `_flag`】`scripts/scan_settings.py` 的
    `KNOWN_READ_HELPERS` 把 `_flag` 登记为"环境开关读取助手"的名字契约，
    于是 `_flag(doc.get("llm_callable"), True)` 会被扫描器当成**开关读取点**、
    参数不是字面量 ⇒ 产出一个 `<unresolved>` 动态家族 ⇒
    `test_settings_registry.py::TestMechanicalZeroGap` 两条零缺口守卫变红（CI 实测）。
    故本助手取 `_as_bool`（不带 env/getenv 词干，正则也匹配不到）。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


# ── 元数据读取加速（P0-1，2026-09-19）────────────────────────────────
# Why 两处都改：
#   1) loader —— 用 C 实现解析。实测 91 个 YAML：纯 Python `SafeLoader` 156.4ms
#      vs `CSafeLoader` 32.8ms（**4.77x**，省 123.6ms），且解析结果逐字段完全相同
#      （已做 JSON 序列化对拍，见 tests/unit/test_load_tool_meta_cache.py）。
#      回落原因：PyYAML 的 C 扩展是可选的（纯 Python 环境没有 `_yaml`），
#      故用 try/except 取 C 实现、缺则回落 —— 与仓库既有的"缺依赖不崩溃"取舍一致
#      （参考 `_as_bool` 的保守默认思路）。
try:  # pragma: no cover - 取决于是否装了 PyYAML 的 C 扩展
    from yaml import CSafeLoader as _YamlLoader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _YamlLoader

# Why 缓存（比换 loader 更关键）：
#   本模块原 docstring 写着"【简易】单次读取，**调用方自行缓存**"，但实际上
#   `agent/tool_gate.py:627`、`agent/rate_limiter.py:76`、`agent/tool_approval.py:354`、
#   `agent/human_in_the_loop/hitl.py:85` **各自维护了一份独立缓存**，互不共享
#   ⇒ 同一次请求里全量扫 YAML 最多发生 **4 次**（4 × 155ms ≈ 620ms）。
#   把缓存下沉到本函数（唯一权威读取入口）后，4 处调用共享同一份结果。
# Why 用 mtime 签名而不是 TTL：
#   本仓库大量存在"改 YAML 立即生效"的使用方式（如 `scripts/sync_capability_manifest.py`
#   之后人工核对、UI 里改 plane/risk）。TTL 会让改动静默延迟生效 —— 那是一个比慢更糟的缺陷。
#   mtime 签名只统计 `数据文件改动 + 目录增删`，因此：
#     · 正常运行（无人改文件）→ 单次 `os.scandir` + stat，**实测 < 1ms**
#     · 改了任何 YAML / 增删文件 → 签名变化 → 自动重读，**语义与改动前完全一致**
_META_CACHE: Dict[str, Dict[str, ToolMeta]] = {}
_META_CACHE_SIG: Dict[str, tuple] = {}


def _defs_signature(root: str) -> tuple:
    """`data/tool_definitions/` 的轻量签名：文件名 + mtime_ns。

    Why 不含文件内容哈希：哈希要把 91 个文件全读一遍（正是我们要避免的开销）。
    `mtime_ns` 精度足以覆盖"人工编辑 YAML"这一唯一现实变更路径；
    极端情况（同一纳秒内改两次）由 `load_tool_meta(force=True)` 兜底。
    """
    entries = []
    with os.scandir(root) as it:
        for e in it:
            if e.name.endswith(".yaml") and e.is_file():
                try:
                    entries.append((e.name, e.stat().st_mtime_ns))
                except OSError:
                    # 读不到 stat 的文件视为"已变化"，逼一次重读而不是静默漏掉
                    entries.append((e.name, -1))
    entries.sort()
    return tuple(entries)


def load_tool_meta(defs_dir: Optional[str] = None, force: bool = False) -> Dict[str, ToolMeta]:
    """读取全部工具 YAML 的能力元数据。

    【不易】缺字段时**给保守默认**（act/execute/medium）而不是崩溃——
            未登记的工具按"会改变世界"对待，安全侧从严。
    【变易】进程级缓存 + mtime 失效：本函数是**唯一权威读取入口**，
            缓存下沉到这里可使原先 4 份独立缓存（tool_gate / rate_limiter /
            tool_approval / hitl）共享同一份结果。
    【简易】`force=True` 可强制重读（治理脚本/测试用）。

    ⚠️ 返回的是**缓存内的同一份 dict 对象**（不是副本）。调用方**不得原地修改**
       返回值；需要修改请自行 `dict(...)` 浅拷贝（既有调用点已是这一用法，
       如 `agent/tool_gate.py:642` 的 `dict(load_tool_meta())`）。
    """
    root = defs_dir or TOOL_DEFS_DIR
    if not os.path.isdir(root):
        return {}
    try:
        sig = _defs_signature(root)
    except OSError:
        # 目录不可读时不缓存、也不返回陈旧数据
        sig = None
    if not force and sig is not None and _META_CACHE_SIG.get(root) == sig:
        return _META_CACHE.get(root, {})

    out: Dict[str, ToolMeta] = {}
    for fname in sorted(os.listdir(root)):
        if not fname.endswith(".yaml"):
            continue
        path = os.path.join(root, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = yaml.load(f, Loader=_YamlLoader)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(doc, dict):
            continue
        name = str(doc.get("name") or os.path.splitext(fname)[0])
        raw_tags = doc.get("tags") or ()
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = tuple(str(t) for t in raw_tags if str(t).strip())
        raw_aliases = doc.get("aliases") or ()
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        # ── 确认分级 override 裁定（TASK-06）──
        # 【不易·为什么裁定放在读取层而不是 `ToolMeta` 的属性里】
        #   `ToolMeta` 是 `frozen=True` 的纯数据容器，属性里做"丢弃无效 override"
        #   会让 `to_dict()` 的 `confirm_level_declared` 与真实 YAML 不一致
        #   （用户改的 YAML 与读到的值不符，排查时误导）。裁定必须在**唯一权威读取
        #   入口**完成一次，此后 `confirm_level` 字段就是"已裁定的声明值"。
        _plane = _norm(doc.get("plane"), PLANES, "act")
        _effect = _norm(doc.get("effect"), EFFECTS, "execute")
        _risk = _norm(doc.get("risk"), RISKS, "medium")
        _declared_cl = _confirm_level_of(doc.get("confirm_level"))
        _cl, _cl_note = _resolve_confirm_level(
            _declared_cl, derive_confirm_level(_plane, _effect, _risk),
            doc.get("confirm_level_reason"))
        out[name] = ToolMeta(
            name=name,
            category=str(doc.get("category") or ""),
            plane=_plane,
            effect=_effect,
            risk=_risk,
            tags=tags,
            description=str(doc.get("description") or "")[:200],
            internal=bool(doc.get("internal", False)),
            tool_type=_norm(doc.get("tool_type"), TOOL_TYPES, "tool"),
            llm_callable=_as_bool(doc.get("llm_callable"), True),
            callable_mode=_norm(doc.get("callable_mode"), CALLABLE_MODES, "auto"),
            permission_level=_norm(doc.get("permission_level"), PERMISSION_LEVELS, ""),
            sandbox_allowed=_as_bool(doc.get("sandbox_allowed"), True),
            isolation_level=_norm(doc.get("isolation_level"), ISOLATION_LEVELS,
                                  DEFAULT_ISOLATION_LEVEL),
            reason=str(doc.get("reason") or "").strip(),
            # ── CapabilitySpec 扩展 ──
            # 【不易·缺 `location` 时给 remote 而不是 local】保守侧从严（见字段注释）。
            # 【不易·`location` 不在这里做事实判定】事实判定在 agent/lines/location.py
            # （要遍历调用链，属于治理脚本/清单的活）；本函数只做**解析**，
            # 保持"唯一权威读取入口"的轻量与确定性。
            location=_norm(doc.get("location"), LOCATIONS, "remote"),
            location_reason=str(doc.get("location_reason") or "").strip(),
            owner=_norm(doc.get("owner"), OWNERS, "builtin"),
            version=str(doc.get("version") or "1.0.0").strip() or "1.0.0",
            namespace=str(doc.get("namespace") or DEFAULT_NAMESPACE).strip() or DEFAULT_NAMESPACE,
            tenant_id=str(doc.get("tenant_id") or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID,
            registry_source=str(doc.get("registry_source") or "global").strip() or "global",
            input_schema=doc.get("schema") if isinstance(doc.get("schema"), dict)
            else (doc.get("input_schema") if isinstance(doc.get("input_schema"), dict) else None),
            output_schema=doc.get("output_schema") if isinstance(doc.get("output_schema"), dict)
            else (doc.get("result_schema") if isinstance(doc.get("result_schema"), dict) else None),
            manifest_version=int(doc.get("manifest_version") or 1),
            signature=str(doc.get("signature") or "").strip(),
            source_trust=str(doc.get("source_trust") or "").strip(),
            compatibility=str(doc.get("compatibility") or "").strip(),
            semver_policy=str(doc.get("semver_policy") or "").strip(),
            health=str(doc.get("health") or "").strip(),
            deprecated=_as_bool(doc.get("deprecated"), False),
            aliases=tuple(str(t) for t in raw_aliases if str(t).strip()),
            # ── 工具侧确认分级（TASK-06）──
            # `_cl_note` 在下面 `logger.info` 里落诊断日志（不静默丢 override）。
            confirm_level=_cl,
            confirm_level_reason=str(doc.get("confirm_level_reason") or "").strip(),
        )
        if _cl_note:
            # 【为什么留日志而不是静默】`_resolve_confirm_level` 可能**丢弃**一个
            # 无效的降级声明（无理由）。若不留痕，用户改了 YAML 却发现"没生效、
            # 也没报错"。这是 D9「解释为什么」在运行时的对应物。
            logger.info("[tool_meta] %s 的 confirm_level override：%s", name, _cl_note)
    if sig is not None:
        _META_CACHE[root] = out
        _META_CACHE_SIG[root] = sig
    return out


def invalidate_tool_meta_cache() -> None:
    """清空元数据缓存（测试与治理脚本用）。

    Why 需要它：`tests/` 里大量用例会临时替换 `data/tool_definitions/`
    （例如 `agent/tool_gate.py:1002` 附近自述"测试若替换了 data/tool_definitions/
    或想验证元数据刷新"）。mtime 签名通常能自动兜住，但同一纳秒内的
    替换 + 恢复会让签名回到原值 —— 显式失效是这种情况下的唯一可靠手段。
    """
    _META_CACHE.clear()
    _META_CACHE_SIG.clear()


@dataclass
class LineProfile:
    """一条主线（可组装的能力档案）

    【主线是权重，不是分区】——同一条工具可以同时被多条主线使用；
    主线只决定"在本次装配里它排多前、要不要被排除"，不决定归属。
    """

    id: str
    name: str = ""
    description: str = ""
    enabled: bool = True

    #: 平面权重（0 = 该平面不参与；越大越优先且召回越多）
    plane_weights: Dict[str, float] = field(default_factory=dict)
    #: 每个平面的**保底召回数**（解决"高优先级平面吃光名额"的饥饿问题）
    plane_floors: Dict[str, int] = field(default_factory=dict)

    #: 主线核心工具：权重加成（可跨平面）
    boost: List[str] = field(default_factory=list)
    #: 明确排除的工具
    mute: List[str] = field(default_factory=list)
    #: 关注的标签：命中的工具获得小幅加成
    tags: List[str] = field(default_factory=list)

    #: 单轮最多暴露给模型的工具数
    max_tools: int = 20

    #: 治理策略
    effect_allow: List[str] = field(default_factory=lambda: ["read", "write", "execute"])
    requires_approval: List[str] = field(default_factory=list)
    #: 是否允许本主线调用 govern 平面（改变自身能力集）
    allow_govern: bool = False

    #: 绑定的技能（skills.json 的 id）
    skills: List[str] = field(default_factory=list)
    #: 绑定的系统提示词片段（可选）
    prompt_note: str = ""

    def __post_init__(self) -> None:
        if not self.plane_weights:
            self.plane_weights = {"resident": 1.0, "perceive": 1.0, "act": 1.0}
        # 归一化平面权重：未知平面丢弃，负值归零
        self.plane_weights = {
            _norm(k, PLANES, ""): float(v)
            for k, v in self.plane_weights.items()
            if _norm(k, PLANES, "")
        }
        self.plane_weights = {k: max(0.0, v) for k, v in self.plane_weights.items()}
        if not self.allow_govern:
            self.plane_weights.pop("govern", None)
        else:
            # 【自洽性】allow_govern=True 必须同时放行 extend 效果，否则治理平面会被
            # effect_allow 过滤成空集 —— 那样"允许治理"就是个静默失效的开关。
            # 注意：放行 ≠ 免确认；确认由 requires_approval 决定（风险仍由 ToolMeta 标注）。
            if "extend" not in self.effect_allow:
                self.effect_allow = list(self.effect_allow) + ["extend"]

    # ── 校验 ──

    def validate(self, known_tools: Optional[set] = None) -> List[str]:
        """返回问题列表（空 = 通过）"""
        issues: List[str] = []
        if not self.id or not isinstance(self.id, str):
            issues.append("id 不能为空")
        if self.max_tools <= 0:
            issues.append("max_tools 必须为正整数")
        for eff in self.effect_allow:
            if eff not in EFFECTS:
                issues.append(f"effect_allow 含非法值: {eff}")
        for plane, floor in self.plane_floors.items():
            if plane not in PLANES:
                issues.append(f"plane_floors 含非法平面: {plane}")
            if floor < 0:
                issues.append(f"plane_floors[{plane}] 不能为负")
        for eff in self.requires_approval:
            if eff not in EFFECTS:
                issues.append(f"requires_approval 含非法值: {eff}")
        if known_tools is not None:
            unknown = [t for t in list(self.boost) + list(self.mute) if t not in known_tools]
            if unknown:
                issues.append(f"引用了未注册的工具: {sorted(unknown)}")
        return issues

    @property
    def max_effect_rank(self) -> int:
        """允许的最高 effect 等级（用于快速判定）"""
        if not self.effect_allow:
            return 0
        return max(_EFFECT_ORDER.get(e, 0) for e in self.effect_allow)

    def allows_effect(self, effect: str) -> bool:
        return _EFFECT_ORDER.get(effect, 99) <= self.max_effect_rank

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "description": self.description,
            "enabled": bool(self.enabled),
            "plane_weights": dict(self.plane_weights),
            "plane_floors": dict(self.plane_floors),
            "boost": list(self.boost),
            "mute": list(self.mute),
            "tags": list(self.tags),
            "max_tools": int(self.max_tools),
            "effect_allow": list(self.effect_allow),
            "requires_approval": list(self.requires_approval),
            "allow_govern": bool(self.allow_govern),
            "skills": list(self.skills),
            "prompt_note": self.prompt_note,
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LineProfile":
        if not isinstance(raw, dict):
            raise ValueError("主线档案必须是字典")
        lid = str(raw.get("id") or "").strip()
        if not lid:
            raise ValueError("主线档案缺少 id")
        # 【坑】不能用 `raw.get("max_tools") or 20`：`0` 是 falsy，会被静默归一成 20，
        # 于是 validate() 的"max_tools 必须为正整数"从 REST 层永远不可达
        # （实测 {"max_tools":0} 会通过校验并静默变成 20）。必须只对 None/空串兜底。
        _mt = raw.get("max_tools")
        try:
            max_tools = 20 if _mt is None or _mt == "" else int(_mt)
        except (TypeError, ValueError):
            raise ValueError(f"max_tools 必须是整数，收到: {_mt!r}")
        return cls(
            id=lid,
            name=str(raw.get("name") or lid),
            description=str(raw.get("description") or ""),
            enabled=bool(raw.get("enabled", True)),
            plane_weights=dict(raw.get("plane_weights") or {}),
            plane_floors={k: int(v) for k, v in (raw.get("plane_floors") or {}).items()},
            boost=[str(t) for t in (raw.get("boost") or [])],
            mute=[str(t) for t in (raw.get("mute") or [])],
            tags=[str(t) for t in (raw.get("tags") or [])],
            max_tools=max_tools,
            effect_allow=[str(e) for e in (raw.get("effect_allow") or ["read", "write", "execute"])],
            requires_approval=[str(e) for e in (raw.get("requires_approval") or [])],
            allow_govern=bool(raw.get("allow_govern", False)),
            skills=[str(s) for s in (raw.get("skills") or [])],
            prompt_note=str(raw.get("prompt_note") or ""),
        )


__all__ = [
    "PLANES", "EFFECTS", "RISKS", "ToolMeta", "LineProfile",
    "load_tool_meta", "invalidate_tool_meta_cache", "TOOL_DEFS_DIR", "AGENT_LINES_DIR",
    "TOOL_TYPES", "CALLABLE_MODES", "PERMISSION_LEVELS",
    "KINDS", "LOCATIONS", "OWNERS", "DEFAULT_TENANT_ID", "DEFAULT_NAMESPACE",
    # ── 工具侧确认分级（TASK-06）──
    "CONFIRM_LEVELS", "CONFIRM_LEVEL_SEMANTICS",
    "derive_confirm_level", "confirm_level_rank",
]
