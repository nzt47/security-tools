"""不可信内容**强制写污点账** —— 工具结果侧的机制 1 接线（TASK-07 第 3 步第 3 项）

【为什么必须写账，否则整个守卫是摆设】

`agent/guardrails/foreign_taint.py::check_text()` 的语义是「**账里没有就放行**」。
而 `mark_foreign` / `mark_foreign_file` / `mark_subagent_output` 在改动前
**生产零调用方**（仅定义 + 测试引用）⇒ 账永远是空的 ⇒ `check_text` **恒放行**
⇒ 即使把 `CP_GUARDRAILS_GUARD_CONTEXT` 打开、把 `guard_tool_execution` 接到执行
路径上，判定结果也永远是"通过"。

TASK-07 §5 把这一条列为**最隐蔽的失败模式**：
    > ✗ 开启 context 守卫但**不写污点账**（`check_text` 会恒放行 ⇒ 守卫是摆设）

本模块就是把账写起来的**唯一入口**。

【为什么写在 `agent/tools/__init__.py::call()` 之后，而不是散在各工具里】

`call()` 是工具分发的**唯一汇聚点**（`tool_gate.py` 的模块 docstring 已论证：
`tool_calling._execute_safe` 与 orchestrator 的直连调用最终都落到这里）。
把"结果打标"放在这里，一次覆盖**全部** 91 个工具 + 全部动态注册的 MCP 工具，
不存在"某个工具忘了打标"的漏点 —— 这正是 E5 要的"三条路径确实被调用"。

【来源分类的唯一口径（D1：不造第二份定义）】

优先读**权威声明** `data/tool_definitions/*.yaml`（经 `agent.lines.models.ToolMeta`）：

    · 注册来源是 MCP（`agent.tools.SOURCE_MCP`）        → `mcp`
    · `category == "knowledge"` 或名字含检索词          → `retrieval`
    · `category == "web"` 且名字含检索词                → `retrieval`
    · `category == "web"`                               → `external_http`
    · `category == "file"` 且 `effect == "read"`        → `file`

其余工具的结果**不打标**（它们返回的是仓库自身算出来的数据：时间、进程列表、
计算结果……把它们全标成"外来"会让守卫到处误拦，那是 E10「零误伤」要禁的）。
**YAML 读不到时降级为空集**（不标任何东西）——与 `_internal_tool_names()` 同款
纪律：宁可少标（守卫退回"恒放行"的旧行为），也不因 YAML 损坏而乱标。

【不易】任何异常都不阻断主路径（打标失败最坏是"没标上"，不是"功能不可用"）。
【变易】`RETRIEVAL_HINTS` 是数据：新增检索类工具的命名约定加一个词。
【简易】纯标准库 + 本包兄弟模块（全部延迟导入）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("agent.guardrails.untrusted_ingest")

#: 工具结果强制打标的开关（**默认开**：打标本身不阻断任何调用）
ENV_MARK_TOOL_RESULTS = "CP_GUARDRAILS_MARK_TOOL_RESULTS"

#: 单个工具结果参与打标的最大字符数（防超长返回撑爆账；与 `MAX_SLICE_CHARS` 同量级）
MAX_MARK_CHARS = 256 * 1024

#: 检索类工具的命名约定（**只用于把 `category=web` 拆成"检索"与"取页"**）
RETRIEVAL_HINTS: Tuple[str, ...] = (
    "search", "recall", "retrieve", "retrieval", "kb_", "knowledge", "memory", "index",
)

#: YAML 读不到时的兜底分类（**故意保守**：只列 §5.7 明确点名的三类来源代表）
#: 【为什么保留这张表】`agent.lines.models.load_tool_meta()` 需要读 91 个 YAML；
#: 若某次调用发生在元数据尚未加载/文件被锁的时刻，返回空集会让**已知的外来来源**
#: 也漏标。兜底表只覆盖"名字本身就说明来源"的那几个，不与 YAML 冲突（YAML 优先）。
_FALLBACK_SOURCES: Dict[str, str] = {
    "web_get": "external_http",
    "web_post": "external_http",
    "web_extract": "external_http",
    "web_batch": "external_http",
    "web_download": "external_http",
    "web_search": "retrieval",
    "browser_navigate": "external_http",
    "kb_search": "retrieval",
    "read_file": "file",
    "search_files": "retrieval",
    "grep": "file",
}

_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def mark_enabled() -> bool:
    """结果打标是否启用（默认开）"""
    return str(os.environ.get(ENV_MARK_TOOL_RESULTS, "1")).strip().lower() \
        not in _DISABLED_VALUES


def _name_is_retrieval(name: str) -> bool:
    low = str(name or "").lower()
    return any(hint in low for hint in RETRIEVAL_HINTS)


def classify_tool(tool_name: str, *, registry_source: str = "") -> str:
    """判定一个工具的返回值属于哪类外来来源；不属于外来时返回空串

    Args:
        tool_name: 工具名。
        registry_source: `agent.tools.registry_facts()[name]["source"]`（可选）。

    Returns:
        `ForeignSource` 值（`mcp` / `retrieval` / `external_http` / `file`）或 `""`。
    """
    name = str(tool_name or "").strip()
    if not name:
        return ""

    # ① MCP：注册来源是权威事实（`register_dynamic(source=SOURCE_MCP, ...)`）
    if str(registry_source or "") == "mcp":
        return "mcp"

    # ② YAML 元数据（唯一权威声明）
    meta = None
    try:
        from agent.lines.models import load_tool_meta
        meta = (load_tool_meta() or {}).get(name)
    except Exception as exc:  # noqa: BLE001  元数据不可用 ⇒ 走兜底表
        logger.debug("[ingest] 工具元数据不可用（走兜底分类）: %s", exc)

    if meta is not None:
        category = str(getattr(meta, "category", "") or "").lower()
        effect = str(getattr(meta, "effect", "") or "").lower()
        if category == "knowledge":
            return "retrieval"
        if category == "web":
            return "retrieval" if _name_is_retrieval(name) else "external_http"
        if category == "file" and effect == "read":
            return "file"
        # 【为什么 `category=extension` 不整体标 mcp】扩展管理工具（`ext_install`）
        # 返回的是安装结论，不是外来内容；把它标成 MCP 会让"安装扩展"这种治理动作
        # 的参数被自己的返回值污染。真正的 MCP 调用结果由 ① 的注册来源判定。
        return _FALLBACK_SOURCES.get(name, "")

    return _FALLBACK_SOURCES.get(name, "")


def extract_text(result: Any, *, limit: int = MAX_MARK_CHARS) -> str:
    """从工具返回值里取出**有界的文本**用于打摘要（不改动返回值）

    【为什么不用 `json.dumps(result)`】返回值里可能有不可序列化的对象、二进制、
    或几千条结果；`json.dumps` 会抛异常或产出巨大的字符串。这里只按已知的
    "承载文本的字段名"取，既稳又可控。
    """
    pieces: List[str] = []
    budget = int(limit)

    def _add(value: Any) -> None:
        nonlocal budget
        if budget <= 0 or value is None:
            return
        text = str(value)
        if not text.strip():
            return
        take = text[:budget]
        pieces.append(take)
        budget -= len(take)

    def _walk(node: Any, depth: int = 0) -> None:
        if budget <= 0 or depth > 4:
            return
        if isinstance(node, str):
            _add(node)
        elif isinstance(node, Mapping):
            for key, value in node.items():
                if str(key).lower() in ("text", "content", "body", "html", "snippet",
                                        "summary", "result", "parsed", "stdout",
                                        "results", "data", "items", "value"):
                    _walk(value, depth + 1)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item, depth + 1)

    try:
        if isinstance(result, str):
            _add(result)
        else:
            _walk(result)
    except Exception as exc:  # noqa: BLE001  提取失败 ⇒ 不标（不阻断主路径）
        logger.debug("[ingest] 结果文本提取失败: %s", exc)
        return ""
    return "\n".join(pieces)


def mark_tool_result(tool_name: str, result: Any, *,
                     registry_source: str = "",
                     ledger: Any = None,
                     surface: str = "") -> Optional[Any]:
    """**把工具结果写入污点账**（机制 1 的结果侧接线；返回新建的标记或 None）

    Args:
        tool_name: 工具名。
        result: 工具返回值（**原样不改**）。
        registry_source: 注册来源事实（可选）。
        ledger: 污点账（缺省进程级账）。
        surface: 调用点标识（审计定位用）。

    Returns:
        `ForeignMark`（标上了）或 `None`（不该标 / 没标上 / 未启用）。
        **绝不抛异常**——打标失败不得阻断工具主路径。
    """
    if not mark_enabled():
        return None
    try:
        source = classify_tool(tool_name, registry_source=registry_source)
        if not source:
            return None
        text = extract_text(result)
        if not text or len(text.strip()) < 16:
            # 【为什么设最小长度】`check_text` 的切片以"行 ≥24 字符"为单位；
            # 过短的返回值（如 `{"ok": true}`）建不出任何摘要片段，
            # 标了也查不中，只会白占账容量。
            return None
        from agent.guardrails.foreign_taint import get_foreign_taint
        active = ledger or get_foreign_taint()
        mark = active.mark(text, source, ref=f"tool:{tool_name}",
                           trace_id=str(surface or ""))
        logger.debug("[ingest] 工具结果已打污点标记 tool=%s source=%s chars=%d",
                     tool_name, source, len(text))
        return mark
    except Exception as exc:  # noqa: BLE001  打标失败不得阻断工具执行
        logger.warning("[ingest] 工具结果打标失败（不影响工具返回）: %s: %s",
                       type(exc).__name__, exc)
        return None


def mark_subagent_result(agent_id: str, text: Any, *, ledger: Any = None) -> Optional[Any]:
    """子智能体输出打标（§5.7 四类来源之一；供子代理链路调用）"""
    try:
        from agent.guardrails.foreign_taint import mark_subagent_output
        return mark_subagent_output(text, agent_id=str(agent_id or ""), ledger=ledger)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ingest] 子智能体输出打标失败: %s", exc)
        return None


def mark_context_items(items: Any, *, source: str = "retrieval",
                       ledger: Any = None, ref: str = "") -> int:
    """把一组上下文条目打标（**检索召回侧的接线入口**；返回打标条数）

    供 orchestrator / 上下文组装侧调用：召回结果进上下文**之前**调一次即可。
    """
    count = 0
    try:
        from agent.guardrails.foreign_taint import get_foreign_taint
        active = ledger or get_foreign_taint()
        for index, item in enumerate(items or ()):
            text = extract_text(item)
            if not text or len(text.strip()) < 16:
                continue
            if active.mark(text, source, ref=f"{ref or 'ctx'}[{index}]") is not None:
                count += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ingest] 上下文条目打标失败: %s", exc)
    return count


def ingest_state() -> Dict[str, Any]:
    """接线状态快照（诊断/验收报告）"""
    return {
        "enabled": mark_enabled(),
        "max_mark_chars": MAX_MARK_CHARS,
        "retrieval_hints": list(RETRIEVAL_HINTS),
        "fallback_sources": dict(_FALLBACK_SOURCES),
    }


__all__ = [
    "ENV_MARK_TOOL_RESULTS", "MAX_MARK_CHARS", "RETRIEVAL_HINTS",
    "mark_enabled", "classify_tool", "extract_text", "mark_tool_result",
    "mark_subagent_result", "mark_context_items", "ingest_state",
]
