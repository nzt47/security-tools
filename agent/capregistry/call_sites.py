"""调用路径的**显式例外表**（`TASK-05` 第 0 步第 2 项 / 交付物 #13）

## 纪律：例外必须**逐点、带理由、带身份**

`TASK-05` §5 明确把"`EXEMPT_CALL_SITES` 写成'什么都放行'的宽泛模式"列为**不通过**。
因此本表的每一条都必须给出四样东西：

| 字段 | 含义 | 缺席后果 |
|---|---|---|
| `reason` | **为什么这条路径可以不经闸门**（不是"暂时先放行"） | `--check` 的腐化检查会报 |
| `identity` | 它以**什么身份**执行（CI/服务账号/人工/外部 MCP 客户端） | 同上 |
| `audit` | 是否产生**结构化审计记录**；没有的必须明说"无" | 同上 |
| `reachable` | 当前**是否可达**（"现实缺口"与"潜在风险"必须分开） | 同上 |

`reachable` 这一维是 `TASK-05` §2.3c 的硬要求：

> **不要把第 4 条算作现实缺口。** 它的事实成立（确实直接 `call_tool`，不过闸门），
> 但**当前不可达**。本任务的 `EXEMPT_CALL_SITES` 与 `--check` 必须**区分
> "现实直调"与"当前不可达的直调"** —— 后者登记为**待接入风险**
> （一旦 SDK 装上就会生效），而不是当已存在缺口处理。

## 锚点为什么是 `路径::符号名` 而不是"路径:行号"

`TASK-05` 的 2026-09-20 预检已经用血淋淋的例子证明了行号会漂移：
本文件所述缺口在任务书写的是 `ci.yml:562`，实测已迁到 **`ci.yml:726`**。
⇒ 用行号做锚点会让"例外表"在某次无关的格式化后**整体失效**，
而失效方式恰恰是**最坏的一种**：`--check` 报"未登记直调" ⇒ 有人去把行号改一遍
（掩盖了真实变化），或者干脆把例外放宽（腐化）。

## 单点可达性的实测依据

`reachability_of()` 把"是否可达"落成**可复算的静态判据**（不靠注释里的断言）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

__all__ = [
    "EXEMPT_CALL_SITES",
    "DEAD_MODULES",
    "VIOLATION_SCOPE_PREFIXES",
    "anchor_key",
    "reachability_of",
]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: `--check` 硬失败的范围：**本地能力平面的生产代码**
#:
#: 【为什么不含 `mcp_services/`】该目录下除 `yunshu_mcp_server.py`（本机 stdio
#:   服务端，**已收录在清单里**）外，其余是**面向外部 MCP 服务端的客户端**与
#:   演示/自测脚本 —— 它们执行的不是本 Registry 管辖的能力。
#:   【不易·为什么仍然要"列出来"而不是"排除掉"】"看得见但不拦"与"看不见"
#:   是两回事：把它们列进 P3 段，等于给未来的"要不要收编 mcp_client"留下证据。
VIOLATION_SCOPE_PREFIXES: tuple = ("agent/", "plugins/", "cloudshu/")

#: **死模块**：**生产代码零调用方**（可达性判据，`scripts/audit_call_paths.py`
#: 会用 `git grep` 实测复核，不靠这里的断言）
#:
#: 依据：`data/capability_manifest.json` 的 `non_capabilities` 已判定
#:   `agent/mcp_executor.py` 的 `McpClient._mock_call` 是 `dead_mock`
#:   （`time.sleep` + 硬编码数据冒充网络调用）。
#:
#: ⚠️ **口径修正（2026-09-20 实测）**：TASK-05 与 TASK-04 的措辞是"零调用方"，
#:   严格核验后应表述为"**生产范围内零调用方**"：
#:   `agent/mcp_executor.py` **确实被** `scripts/check_mcp_log_level.py`
#:   （一个只调日志级别的运维脚本）import。判据因此定为"`agent/`、`plugins/`、
#:   `cloudshu/` 内有无 importer"，而不是字面上的"零 importer"——
#:   后者实测会把该模块误判为可达。这条修正记录在
#:   `scripts/audit_call_paths.py::_reachability` 的注释里。
DEAD_MODULES: frozenset = frozenset({
    "agent/mcp_executor.py",
    "mcp_services/yunshu_mcp_bridge.py",
})


def anchor_key(file: str, symbol: str) -> str:
    """生成例外表/清单的**稳定锚点**：`相对路径::符号名`

    归一化：正斜杠、去掉 `./` 前缀、符号名取最后一段（`A::b` 与 `A::c::b` 归一，
    因为 AST 作用域链的形状会随重构变化，而"哪个函数"才是要钉住的事实）。
    """
    f = str(file or "").replace("\\", "/").lstrip("./")
    leaf = str(symbol or "").split("::")[-1].strip() or "<module>"
    return f"{f}::{leaf}"


def reachability_of(file: str, symbol: str, callee: str = "",
                    path_kind: str = "") -> Tuple[bool, str]:
    """静态判定该路径**当前是否可达**（不执行任何代码）

    这是给**外部调用方**（测试、报表）用的便捷版本；真正的可达性判定在
    `scripts/audit_call_paths.py::_reachability`（它会额外用 `git grep` 复核
    死模块的 importer 数）。

    判据（按优先级）：
      1. **远程原语**一律取决于官方 `mcp` SDK 是否存在 —— 实测未安装 ⇒ 不可达
         （`agent/skills_mgmt/mcp_adapter.py::_check_mcp_sdk()` 是那条路径的入口闸）。
      2. 其余路径按"可达"处理，并在返回值里注明依据。

    【为什么必须实测 SDK 而不是读注释】`TASK-05` §2.3c 的第 4 条正是靠
    "实测未安装"才从"现实缺口"降级为"潜在风险"。硬编码一个 `False` 会在
    SDK 装上后**静默过期**（那就成了"一旦 SDK 装上即生效"的反面教材）。
    """
    if path_kind == "remote_primitive":
        try:
            import mcp  # noqa: F401,PLC0415  探测：装没装官方 SDK
            return True, "官方 mcp SDK 已安装 ⇒ 该远程直调路径**当前可达**"
        except ImportError:
            return False, ("官方 mcp SDK 未安装（实测 ImportError）⇒ "
                           "该远程直调路径当前不可达；**一旦装上即生效**")
    if file in DEAD_MODULES:
        return False, f"死模块（`{file}` 生产代码零 importer，见 DEAD_MODULES）"
    return True, "静态判定为可达"


# ════════════════════════════════════════════════════════════
#  例外表
# ════════════════════════════════════════════════════════════
#
# 键 = anchor_key(file, symbol)；值 = 四要素（reason / identity / audit / reachable）
#
# ⚠️ 每条都必须是**单点**。禁止写成目录级、通配符级、或"整个模块放行"。

EXEMPT_CALL_SITES: Dict[str, Dict[str, Any]] = {
    # ── ① CI 面的知识库审计 CLI（TASK-05 §2.3b 的"现实缺口 #1"）───────────
    #
    # 事实：`.github/workflows/ci.yml` 的 `knowledge-audit-smoke` job 直调
    #      `python -m agent.knowledge audit` ⇒ `agent/knowledge/__main__.py::cmd_audit`
    #      ⇒ `run_knowledge_audit(...)`，**不过 `_registry`、不过 `tool_gate`**。
    # 处置（TASK-05 §3 第 0 步第 4 项"保留两个入口，但共用同一实现 + 同一审计"）：
    #      两个入口已收敛到 `agent/knowledge/audit_entry.py::run_knowledge_audit_entry`
    #      （唯一实现），CI 面**产生结构化审计记录**（`data/audit/knowledge_audit.jsonl`）。
    #      保留 CLI 入口是**设计意图**：CI 不该依赖"模型可见集"，也不该被审批边界
    #      拦在门外（那是给交互式高危动作设计的）。
    "agent/knowledge/__main__.py::cmd_audit": {
        "reason": (
            "CI/运维 CLI 的**设计入口**（`python -m agent.knowledge audit`）："
            "知识库健康巡检是只读诊断动作，本就不该受'模型可见集'与'交互式审批边界'"
            "约束。收敛方式不是'取消该入口'，而是让 CI 面与 Agent 面**共用同一实现 + "
            "同一审计**（已落地：agent/knowledge/audit_entry.py）"
        ),
        "identity": "ci（CI job `knowledge-audit-smoke`）/ 人工运维者",
        "audit": ("有：`run_knowledge_audit_entry()` 无论成败都写一条结构化审计记录到 "
                  "`data/audit/knowledge_audit.jsonl`（含 channel/actor/参数摘要/结果摘要）"),
        "reachable": True,
        "path_kind": "direct",
    },
    "agent/knowledge/tools.py::kb_lint": {
        "reason": (
            "Agent 面的同名能力（`kb_lint`）：它**注册在 `_registry` 里**，"
            "正常调用会经 `tools.call()`；此处登记的是它与 CI 面"
            "**共用实现**的那一层（`run_knowledge_audit_entry`），"
            "两者参数适配后落到同一份审计与同一份判定"
        ),
        "identity": "llm（模型经 tools.call 触发）/ system",
        "audit": "有：同一条结构化审计记录（channel=agent_tool）",
        "reachable": True,
        "path_kind": "direct",
    },

    # ── ② 后台任务执行器的收口路径（TASK-05 §2.3c 的"现实缺口 #2"）─────────
    #
    # ⚠️ **实测更正（D10：发现相反证据要记录并上报）**：
    #   `agent/async_executor.py:22` 是 `from agent.tools import call as call_tool`
    #   ⇒ 第 224 行的 `call_tool(tool_name, **params)` **确实过 `tools.call()`、
    #   也确实过 `tool_gate`**。它**不是**"绕过闸门"。
    #   真正的缺口是 **`submit()` 没有身份参数**，且 `ThreadPoolExecutor`
    #   **不继承 `contextvars`** ⇒ 执行线程里 `current_session_source()` 为空、
    #   `session_source` 落到环境变量缺省值 "cli"（= 一次后台调用被当成"人从 CLI 调的"）。
    # 处置：本任务给出**身份透传范式**（`agent/capregistry/invoke.py` 的
    #   `set_session_source` 包夹 + `IDENTITY_SESSION_SOURCE` 映射），并把它登记在此。
    "agent/async_executor.py::_run_task": {
        "reason": (
            "**不是绕过**：经 `from agent.tools import call as call_tool` 落到 "
            "`agent/tools/__init__.py::call()` ⇒ 闸门/限流/审计全部生效。"
            "缺口是**身份缺失**（`submit()` 无身份参数 + 线程池不继承 contextvars ⇒ "
            "`session_source` 退化为环境变量缺省 'cli'）。"
            "本任务已给出身份透传范式（`agent/capregistry/invoke.py`），"
            "**完整身份层修复属 TASK-06**"
        ),
        "identity": "system（后台/定时任务）—— 当前**未能如实上报**，见 reason",
        "audit": "部分：`tools.call()` 的 trace/审计生效；但身份字段不准确",
        "reachable": True,
        "path_kind": "funnel_no_identity",
    },

    # ── ③ MCP 服务端的 tools/call（TASK-05 §2.3c 的"现实缺口 #3"）──────────
    #
    # ⚠️ **实测更正（同上）**：`mcp_services/yunshu_mcp_server.py:472-476` 也是
    #   `from agent import tools as _tools` + `_tools.call(...)` ⇒ **过闸门**。
    #   它文档里说"不做鉴权"指的是**协议层不校验调用方身份**
    #   （MCP 协议本身没有内建认证），而不是"绕过工具闸门"。
    #   故这是一条**有意的例外**：外部 MCP 客户端的调用以"未具名外部身份"进入。
    "mcp_services/yunshu_mcp_server.py::_handle_tools_call": {
        "reason": (
            "**不是绕过**：经 `agent.tools.call()` ⇒ 过闸门（被拒时返回 "
            "`isError: true` 的 JSON-RPC 结果）。"
            "例外点是**协议层身份**：MCP 协议本身没有内建认证，"
            "故外部客户端的调用无法携带仓库身份语义 —— "
            "这是**有意保留**的（本机 stdio 服务，且只读清单默认白名单）"
        ),
        "identity": "外部 MCP 客户端（协议无认证 ⇒ 视为未具名外部身份）",
        "audit": "有：`logger.info` 记录工具名与参数键；`tools.call()` 的审计链生效",
        "reachable": True,
        "path_kind": "funnel_no_identity",
    },

    # ── ④ 技能 MCP 适配器的远程直调（TASK-05 §2.3c 的"潜在风险 #4"）────────
    # 真实符号是 `McpSkillAdapter._call_tool`（`mcp_adapter.py:228`），
    # 第 254 行 `result = session.call_tool(tool_name, params)` 在其中。
    "agent/skills_mgmt/mcp_adapter.py::_call_tool": {
        "reason": (
            "**待接入风险，不是现实缺口**：它确实直接 `session.call_tool(...)`"
            "（不经 `_registry`、不过 `tool_gate`），但整条路径依赖官方 `mcp` SDK，"
            "而 SDK **实测未安装** ⇒ `_check_mcp_sdk()` 抛 "
            "`SkillMcpError(MCP_SDK_UNAVAILABLE)` ⇒ 当前不可达。"
            "**一旦 SDK 装上即生效** ⇒ 接入本 Registry/Loader 是 TASK-07 的前置项"
        ),
        "identity": "system（技能链路的远端工具调用）",
        "audit": "无（该路径当前不可达，尚未产生任何调用记录）",
        "reachable": False,
        "path_kind": "remote_primitive",
        "callee": "session.call_tool",
    },

    # ── ④b 死模块里的 MockClient 直调（**零调用方**）─────────────────────
    # 依据：`data/capability_manifest.json` 的 `non_capabilities` 判定
    #   `agent/mcp_executor.py::McpClient._mock_call` 为 `dead_mock`：
    #   “用 time.sleep + 硬编码数据冒充网络调用；agent/、plugins/、scripts/ 内零调用方”。
    # 可达性由 `DEAD_MODULES` + `git grep` 实测复核（不靠本注释的断言）。
    "agent/mcp_executor.py::execute": {
        "reason": (
            "**死模块**：`agent/mcp_executor.py` **生产范围内零调用方**"
            "（实测 `git grep -l agent.mcp_executor` 只命中自身、"
            "`scripts/check_mcp_log_level.py` 这个只调日志级别的运维脚本，"
            "以及测试）⇒ 该 `client.call_tool` 的 MockClient 路径不可达。"
            "TASK-04 已把它判定为 `dead_mock`，**不登记为能力**"
        ),
        "identity": "无（死代码）",
        "audit": "无（不可达）",
        "reachable": False,
        "path_kind": "remote_primitive",
        "callee": "client.call_tool",
    },
    "agent/mcp_executor.py::<module>": {
        "reason": (
            "同上：`agent/mcp_executor.py` 是死模块（生产范围内零调用方），"
            "模块级的 `client.call_tool` 自检代码不可达"
        ),
        "identity": "无（死代码）",
        "audit": "无（不可达）",
        "reachable": False,
        "path_kind": "remote_primitive",
        "callee": "client.call_tool",
    },

    # ── ⑤ 本任务新增的两条入口（自身即收口实现，不是"绕过"）───────────────
    "agent/server_routes/routes_capabilities.py::api_capabilities_invoke": {
        "reason": (
            "`/capabilities/invoke` **自身就是统一调用入口**，其实现"
            "（`agent/capregistry/invoke.py::invoke_capability`）对本地能力"
            "**唯一地**调用 `agent/tools/__init__.py::call()`（见 loader.LocalLoader）"
        ),
        "identity": "由请求体/请求头显式声明（llm/human/system/service_account）",
        "audit": "有：`tools.call()` 的 trace/限流/审批记录；HTTP 层有 trace_route",
        "reachable": True,
        "path_kind": "funnel",
    },
    "agent/capregistry/invoke.py::invoke_capability": {
        "reason": (
            "统一调用实现本身：对 `location=local` 的能力经 `LocalLoader._do_invoke` "
            "→ `agent.tools.call()`；对 `location=remote` 走 Loader 传输层。"
            "它是**收口的定义处**，不是绕过点"
        ),
        "identity": "入参 `identity`（默认 human），映射到 tool_gate 的 session_source",
        "audit": "有：见上；另 `meta.timing`/`meta.contract` 提供可观测字段",
        "reachable": True,
        "path_kind": "funnel",
        "via_registry": True,
        "via_gate": True,
    },

    # ── ⑥ MCP 工具注册转发（**本轮新发现，不在 TASK-05 已知的 4 处之内**）────
    #
    # 事实：`agent/tools/mcp_connector.py::_make_stdio_handler` 返回的 `_handler`
    #   经 `register_dynamic(..., source="mcp")` **注册进 `_registry`**，
    #   其内部把调用转发给 `mcp_services.mcp_client.MCPClient.call_tool`。
    # 判定：**它不是绕过**——因为 `_handler` 是注册表里的 handler，
    #   任何调用方都要经 `tools.call()`，闸门照常生效。缺的是**传输层**
    #   （`TASK-05` §2.2 的结论：MCP 传输层无熔断、无连接状态机）。
    #   把它收敛到 `Loader` 属 **TASK-07**（本任务只暴露并登记）。
    # 可达性：`mcp_client` 有生产 importer（`mcp_connector.py:56`）⇒ **可达**。
    "agent/tools/mcp_connector.py::_handler": {
        "reason": (
            "**不是绕过闸门**：该 `_handler` 经 `register_dynamic(source='mcp')` "
            "注册进 `_registry`，调用方必经 `agent.tools.call()` ⇒ "
            "闸门/限流/审计照常生效。缺的是**传输层治理**"
            "（MCP 无熔断、无连接状态机、无退避）—— "
            "收敛到本任务的 `Loader` 属 **TASK-07**，本任务只登记并暴露"
        ),
        "identity": "由调用方决定（模型/人/后台）；`_handler` 自身不携带身份",
        "audit": "有：经 `tools.call()` ⇒ trace/限流/审计生效；传输层无独立审计",
        "reachable": True,
        "path_kind": "remote_primitive",
        "via_registry": True,
        "via_gate": True,
        "callee": "client.call_tool",
    },
}
