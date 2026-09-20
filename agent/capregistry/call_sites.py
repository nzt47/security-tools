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
    "IDENTITY_BYPASS_FACES",
    "identity_bypass_face",
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
    # 【2026-09-21 补登记】TASK-08 给 MCP 调用加了超时/重试预算时，把原来的 SDK 调用路径
    # 重构成了 `_build()` 内层闭包（`agent/skills_mgmt/mcp_adapter.py:372`），
    # 于是扫描器把它识别为**新的调用点** —— TASK-05 登记的是旧符号 `_call_tool`，已不存在。
    # ⇒ 这是"重构导致登记锚点漂移"，不是新增了一条绕过。
    "agent/skills_mgmt/mcp_adapter.py::_build": {
        "reason": (
            "**远程原语的 SDK 调用点**：`session.call_tool(...)` 走官方 `mcp` SDK 的 "
            "`ClientSession`。它**不经** `_registry`、**不过** `tool_gate`，"
            "因为它是**技能侧 MCP 适配层**的一部分（技能调远端 MCP 工具），"
            "与 `agent.tools` 的能力注册面是两条不同的链路。"
            "可达性由 `_check_mcp_sdk()`（`:59`）把闸 —— **实测 `import mcp` 抛 ImportError**"
            "（`No module named 'mcp'`）⇒ **当前不可达**，属"
            "「**潜在风险、当前不可达**」，与 TASK-05 §2.3c 第 4 条同一处置口径。"
            "⚠️ **一旦装上官方 SDK 该路径即生效**，届时应把它接入熔断/身份/审计后再登记为可达"
        ),
        "identity": "无 —— 技能侧调用不经会话身份传播（装上 SDK 后需补）",
        "audit": "无独立审计（当前不可达故未触发）；装上 SDK 后需接入",
        "reachable": False,
        "path_kind": "remote_primitive",
    },
    # 【2026-09-21 补登记】全量回归时 `--check` 报出这一处未登记直调。
    # ⚠️ 诚实说明：该文件**自 2026-06-29 起未被改过**（`git log -1 -- mcp_services/test_mcp_windows.py`
    #    = `936bfd7d`），故它**不是本次新增的直调**，而是扫描面/判定条件在后续任务中变化后
    #    才被识别出来。原实现漏检的原因未逐行追查（超出本轮范围），此处按"事实发现"登记。
    "mcp_services/test_mcp_windows.py::test_error_mode": {
        "reason": (
            "**独立的手工 MCP 客户端兼容性脚本**，不是能力、不是入口、不被任何链路 import。"
            "它自己 `MCPClient(...)` 起一个进程内客户端并直接 `client.call_tool(...)`，"
            "用途是人工验证 Windows 下的超时/重试/编码行为（文件 docstring 写明"
            "`python test_mcp_windows.py` 手工运行）。"
            "⇒ 它**不经** `tools.call()` 也不应经（否则就不是在测 MCP 传输层本身了）。"
            "另：`pytest.ini` 的 `testpaths = tests` ⇒ 本文件**不被 pytest 收集**，"
            "且 `mcp_services/` **不在** `VIOLATION_SCOPE_PREFIXES`（硬失败范围仅 "
            "`agent/`、`plugins/`、`cloudshu/`）⇒ 它只被列出、不阻断门禁。"
            "登记的目的是让 `--check` 的**未登记数归零**，使真正的生产缺口不被噪声掩盖"
        ),
        "identity": "手工运行者（人在终端执行脚本）；脚本内无身份传播",
        "audit": "无独立审计 —— 但它是**离线的兼容性验证脚本**，其调用不进入生产可观测面；"
                 "若要审计，应给它绑一个显式 session_source 后再登记（当前不需要）",
        "reachable": True,
        "path_kind": "direct",
    },
}


# ════════════════════════════════════════════════════════════
#  TASK-06 §3 第 5 步 (b)：**4 条绕过会话身份的工具调用面**
# ════════════════════════════════════════════════════════════
#
# ## 为什么单独一张表，而不是塞进 `EXEMPT_CALL_SITES`
#
# 两张表的**语义完全不同**：
#   · `EXEMPT_CALL_SITES` = "AST 扫描器**看得见**的、绕过 `_registry`/`tool_gate`
#     的直调，逐点给出为什么可以放行"。它的消费者是
#     `scripts/audit_call_paths.py --check`（未登记即硬失败）。
#   · 本表 = "扫描器**看不见**的、**身份维度**的缺口"。这四条不是"绕过闸门"，
#     而是"**表达不出身份**"—— 实测：
#       `python -c "...audit_call_paths.scan()..."` 的 38 个锚点里
#       **没有** `agent/task_scheduler.py`、也**没有**工作流执行器那两个 lambda。
#     把看不见的东西塞进"看得见清单"会制造两种坏结果：要么让腐化守卫误报，
#     要么让人以为"清单已覆盖全部身份缺口"（**假绿**，D12 那一类）。
#
# ## 硬要求（TASK-06 E14）
#
#   ① 每条**显式声明身份来源**；
#   ② 每条**进入审计**（`wired=True` 的经身份透传 ⇒ `tool_gate` 的
#      `tool.confirm_decision` 审计记录里带真实 identity/source；
#      `wired=False` 的必须写明"为什么现在没有审计"与风险）；
#   ③ **至少**面 2 与面 3 接到本任务的身份层（已做到）；
#   ④ 面 1、面 4 若未修 ⇒ **登记为待办并写明风险**（已做到，不得略过）。

#: 面 1：定时命令的子进程 —— **已修**（TASK-06）
#: 事实：`agent/task_scheduler.py::_run_task_body` 用
#:   `subprocess.Popen(command, shell=True)` 执行 `system_command` 任务。
#:   `_guard_scheduled_command`（`:252-303`）在 Popen **之前**做权限判定且 fail-closed，
#:   但子进程**继承服务进程身份、无降权**。
#: TASK-06 处置：**已修** —— `run_task()` 在执行线程内上报
#:   `set_session_source("scheduled")` 与 `set_execution_identity("system")`，
#:   使这条路径在**审计与闸门**两个维度上都有真实身份（原状是"来源只在严格模式下
#:   被消费，且严格模式默认关闭 ⇒ 该上报等于失效"）。
#: 【残留（如实登记）】**子进程仍未降权** —— 降权需要 Windows 专用令牌操作
#:   （`CreateProcessAsUser` / `icacls`），属 TASK-07 沙箱统一收口范围。
#:   风险：被批准执行的高危命令以服务进程同等权限运行；缓解 = 该路径本身受
#:   `_guard_scheduled_command`（critical 拒绝 / warning 走权限系统）约束，
#:   且 `system_command` 任务的创建面当前**无任何在跑的实例**（实测 0 条）。

#: 面 4：MCP 服务端的 tools/call —— **协议层无身份，已登记为待办**
#: 事实：`mcp_services/yunshu_mcp_server.py::_handle_tools_call` 经
#:   `agent.tools.call()` ⇒ **过闸门**；但 MCP 协议本身**没有内建认证**，
#:   服务端无法得知调用方是谁。
#: TASK-06 处置：**不修**（修它等于自造一个 MCP 鉴权协议，超出范围且会破坏
#:   与标准 MCP 客户端的互操作）+ **登记为待办**。
#: 风险（如实写明）：任何能连上该 stdio 服务端的进程都可调用其暴露的工具集。
#:   缓解：① 它是**本机 stdio** 服务（非网络监听）；② `exposed_tools` 默认白名单；
#:   ③ 它经 `tools.call()` ⇒ 闸门/审批/限流/审计照常生效，且现在会带
#:   `session_source="mcp"`（TASK-05 补），审计里可与其它来源区分。
#: 待办归属：**TASK-07**（安全接线）或独立的"MCP 鉴权"任务。

IDENTITY_BYPASS_FACES: Dict[str, Dict[str, Any]] = {
    # ── 面 1 ──────────────────────────────────────────────────────────
    "agent/task_scheduler.py::_run_task_body": {
        "face": 1,
        "title": "定时命令的 subprocess（子进程继承服务进程身份，无降权）",
        "identity_source": (
            "**已声明（TASK-06 接入）**：`run_task()` 在执行线程内上报 "
            "`set_session_source(\"scheduled\")` + `set_execution_identity(\"system\")`；"
            "子进程本身**仍**继承服务进程身份（未降权）"
        ),
        "wired": True,
        "audit": (
            "有：`run_task` 的上报使 `tool_gate` 的确认决策审计（action="
            "`tool.confirm_decision`）带上 identity=system / source=scheduled；"
            "任务执行结果另经 `_append_history` 落 `data/schedule_history.jsonl`"
        ),
        "risk": (
            "**残留**：被批准执行的高危命令以服务进程同等权限运行（无降权）。"
            "缓解：`_guard_scheduled_command` 在 Popen 之前做 fail-closed 权限判定；"
            "且实测当前**没有任何在跑的定时任务**（`data/schedules.json` 0 条）"
        ),
        "todo": "子进程降权（Windows 令牌 / icacls）—— 归 TASK-07 沙箱统一收口",
    },
    # ── 面 2 ──────────────────────────────────────────────────────────
    "agent/async_executor.py::_run_task": {
        "face": 2,
        "title": "AsyncExecutor.submit() 无身份参数，且经 submit_task 开放给模型",
        "identity_source": (
            "**已声明（TASK-06 接入）**：`submit()` 新增 `identity` 参数"
            "（与 TASK-05 的 `session_source` 并列）；`code_tools.submit_task` "
            "缺省上报 `llm`（本工具是模型面工具）并继承上游已声明的身份；"
            "工作线程内 `set_execution_identity` 包夹（contextvars 不跨线程）"
        ),
        "wired": True,
        "audit": (
            "有：身份随任务落盘（task 记录的 `identity`/`session_source` 字段），"
            "且 `tool_gate` 的 `tool.confirm_decision` 审计带真实 identity"
        ),
        "risk": "无残留（本条是 TASK-06 明令'至少接到身份层'的两条之一）",
        "todo": "",
    },
    # ── 面 3 ──────────────────────────────────────────────────────────
    "agent/server_routes/routes_workflow_learning.py::_svc": {
        "face": 3,
        "title": "工作流回放真执行工具，但执行器是裸 lambda（不透传 actor/session）",
        "identity_source": (
            "**已声明（TASK-06 接入）**：两处注入点"
            "（`routes_workflow_learning.py::_svc` 与 "
            "`orchestrator/orchestrator.py` 的懒注入）改用 "
            "`agent/capregistry/invoke.py::identity_propagating_executor`，"
            "在**被调用的那一线程内**读出当前身份并如实上报（上下文优先、参数兜底）；"
            "HTTP 面缺省 `human`，编排面缺省 `llm`"
        ),
        "wired": True,
        "audit": (
            "有：`tools.call()` 的 trace/限流/审批审计 + `tool.confirm_decision` "
            "带真实 identity（原状是 identity 为空、source 落到缺省 'cli'）"
        ),
        "risk": "无残留；`@require_token` 仍只管'能否进端点'，'以谁执行'由本层如实上报",
        "todo": "",
    },
    # ── 面 4 ──────────────────────────────────────────────────────────
    "mcp_services/yunshu_mcp_server.py::_handle_tools_call": {
        "face": 4,
        "title": "MCP 服务端 tools/call（协议层无内建认证）",
        "identity_source": (
            "**协议层不可知（有意保留）**：MCP 协议本身没有内建认证，"
            "服务端无法得知调用方身份。已声明的最近似身份 = "
            "`session_source=\"mcp\"`（TASK-05 补）+ 未具名外部身份"
        ),
        "wired": False,
        "audit": (
            "部分：经 `tools.call()` ⇒ 闸门/审批/限流/审计生效，且审计里"
            "`session_source=mcp` 可区分；但**没有**调用方身份（协议给不出）"
        ),
        "risk": (
            "**登记为待办**：任何能连上该 stdio 服务端的本机进程都可调用其暴露的工具集。"
            "缓解：① 本机 stdio 服务（非网络监听）；② `exposed_tools` 默认白名单；"
            "③ 闸门/审批对所有调用生效"
        ),
        "todo": "MCP 层鉴权（验签后换发短效内部 token）—— 归 TASK-07 或独立任务",
    },
}


def identity_bypass_face(anchor: str) -> Dict[str, Any]:
    """按锚点取一条身份绕过面的登记（不存在 ⇒ 空 dict，**不抛异常**）"""
    return dict(IDENTITY_BYPASS_FACES.get(str(anchor or "").strip(), {}))
