"""工具注册模块 — 核心工具（状态、记忆、感知、人格）"""
import logging
import os
from agent import tools as _tools

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  search_memory 的两条腿（scope 分派）
#
#  背景：`expand_context`（原注册于本文件）与 `search_memory` 是**真部分重复**——
#  `expand_context_from_memory` 内部的 `_vector_memory.search(query, top_k)` 正是
#  `search_memory` 链路的第一段（`DigitalLifeStateMixin._combined_search` 的向量腿），
#  差别只在于 search_memory 还多一条事件日志腿（`_memory.query_logs`）。
#  故按第 0 档合并：expand_context 的能力收进 search_memory 的 `scope="vector"`，
#  净减 1 个工具且**不丢任何一条腿**。
# ════════════════════════════════════════════════════════════

#: 合法 scope（默认 all = search_memory 原有行为：两条腿都走）
_SEARCH_MEMORY_SCOPES = ("all", "vector", "logs")

#: 各 scope 的默认条数上限。
#: all/logs 沿用 search_memory 原口径（`_combined_search(limit=10)`）；
#: vector 沿用 expand_context 的 `max_items=5`。
_SEARCH_MEMORY_DEFAULT_LIMIT = {"all": 10, "vector": 5, "logs": 10}


def _vector_leg(dl, query, limit):
    """向量（语义）腿 —— 复用 `expand_context_from_memory`，即 `_vector_memory.search` 的既有封装。

    Returns:
        (hits, error)：hits 元素恒为 ``{"content", "score"}``，与 expand_context
        原来返回的 ``items`` 逐项同形；error 为非 None 时表示该腿整体不可用
        （向量记忆未启用 / 检索异常），对齐 expand_context 的失败语义。
    """
    from agent.system_tools import expand_context_from_memory

    res = expand_context_from_memory(dl, query, limit)
    hits = []
    for item in (res.get("items") or []):
        if isinstance(item, dict):
            hits.append({"content": item.get("content", ""), "score": item.get("score", 0)})
        else:
            hits.append({
                "content": getattr(item, "content", ""),
                "score": getattr(item, "score", 0),
            })
    return hits, (None if res.get("ok") else res.get("error"))


def _log_leg(dl, query, limit):
    """事件日志腿 —— 黑匣子 ``_memory.query_logs(search=...)``（与 `_combined_search` 第二条腿同源）。

    Returns:
        (hits, error)：hits 元素为 ``{"event_type", "timestamp", "data"}``。
    """
    memory = getattr(dl, "_memory", None)
    if not memory:
        return [], "事件日志系统未启用"
    try:
        raw = memory.query_logs(search=query, limit=limit) or []
    except Exception as e:
        logger.error("事件日志检索失败: %s", e)
        return [], str(e)
    hits = []
    for entry in raw:
        if isinstance(entry, dict):
            hits.append({
                "event_type": entry.get("event_type", "?"),
                "timestamp": entry.get("timestamp", ""),
                "data": entry.get("data", {}),
            })
        else:
            hits.append({"event_type": "?", "timestamp": "", "data": str(entry)})
    return hits, None


def _format_vector_hits(query, hits, limit):
    """scope=vector 的文本形态（沿用 `_combined_search` 的排版口径：来源标签 + 200 字截断）"""
    if not hits:
        return f"没有找到与 '{query}' 相关的语义记忆。"
    lines = [f"找到 {len(hits)} 条相关语义记忆："]
    for h in hits[:limit]:
        lines.append(f"  🧠 语义记忆 {str(h.get('content', ''))[:200]}")
    return "\n".join(lines)


def _format_log_hits(query, hits, limit):
    """scope=logs 的文本形态（沿用 `_combined_search` 的 📋 事件日志 排版口径）"""
    if not hits:
        return f"没有找到与 '{query}' 相关的事件日志。"
    lines = [f"找到 {len(hits)} 条相关事件日志："]
    for h in hits[:limit]:
        lines.append(f"  📋 事件日志 [{h.get('event_type', '?')}] {str(h.get('data', {}))[:200]}")
    return "\n".join(lines)


def register_planning_tools(dl):
    """注册规划工具到 dl._planning_tools

    Args:
        dl: DigitalLife 实例（必须已初始化 _planning_tools）
    """
    if not hasattr(dl, '_planning_tools') or dl._planning_tools is None:
        logger.warning("规划工具注册跳过: _planning_tools 未初始化")
        return

    try:
        @dl._planning_tools.register("check_health", "检查身体状态")
        def _check_health_tool(**kwargs):
            readings = dl.check_health()
            return {"ok": True, "data": dl.body.get_health_report()}

        @dl._planning_tools.register("get_status", "获取完整状态")
        def _get_status_tool(**kwargs):
            return {"ok": True, "data": dl.get_status()}

        @dl._planning_tools.register("search_memory", "搜索记忆")
        def _search_memory_tool(**kwargs):
            query = kwargs.get("query", "")
            if not query:
                return {"ok": False, "error": "请提供搜索关键词"}
            return {"ok": True, "data": dl._combined_search(query)}

        @dl._planning_tools.register("get_sensor_summary", "获取传感器摘要")
        def _get_sensor_summary_tool(**kwargs):
            return {"ok": True, "data": dl.body.get_sensor_summary()}

        @dl._planning_tools.register("llm_chat", "进行对话")
        def _llm_chat_tool(**kwargs):
            response_text = kwargs.get("response", "")
            return {"ok": True, "data": response_text}

        logger.info("规划工具注册完成: %s", dl._planning_tools.list_tools())

    except Exception as e:
        logger.warning("规划工具注册失败: %s", e)


def register_all(dl):
    """注册所有核心工具（规划工具 + 常规工具）

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    # 先注册规划工具
    register_planning_tools(dl)

    # ════════════════════════════════════════════════════════════
    #  常规工具（注册到全局 tools 注册表）
    # ════════════════════════════════════════════════════════════

    @_tools.register("get_status", "获取我的完整状态", schema={
        "type": "object",
        "properties": {},
    })
    def _get_status(**kwargs):
        return dl.get_status()

    @_tools.register("search_memory",
                     "搜索我的记忆。scope=all（默认）同时检索语义记忆与事件日志；"
                     "scope=vector 只做语义（向量）检索；scope=logs 只查事件日志。",
                     schema={
                         "type": "object",
                         "properties": {
                             "query": {"type": "string", "description": "搜索关键词"},
                             "scope": {
                                 "type": "string",
                                 "enum": ["all", "vector", "logs"],
                                 "description": "检索范围：all=语义记忆+事件日志（默认），vector=仅语义记忆，logs=仅事件日志",
                             },
                             "max_items": {"type": "integer",
                                           "description": "返回条数上限，默认 all/logs=10，vector=5"},
                         },
                         "required": ["query"],
                     })
    def _search_memory(**kwargs):
        query = kwargs.get("query", "")
        scope = kwargs.get("scope")
        if scope is None:
            scope = "all"
        elif isinstance(scope, str):
            scope = scope.strip().lower()
        raw_max = kwargs.get("max_items")

        if not query:
            return {"ok": False, "error": "请提供搜索关键词", "query": query, "scope": scope}
        if scope not in _SEARCH_MEMORY_SCOPES:
            return {
                "ok": False,
                "error": f"非法的 scope: {scope!r}，可选值: {' / '.join(_SEARCH_MEMORY_SCOPES)}",
                "query": query,
                "scope": scope,
                "allowed_scopes": list(_SEARCH_MEMORY_SCOPES),
            }
        if raw_max is None:
            limit = _SEARCH_MEMORY_DEFAULT_LIMIT[scope]
        else:
            try:
                limit = int(raw_max)
            except (TypeError, ValueError):
                return {"ok": False, "error": "max_items 必须是正整数",
                        "query": query, "scope": scope}
            if limit <= 0:
                return {"ok": False, "error": "max_items 必须为正整数",
                        "query": query, "scope": scope}

        # ── 两条腿按 scope 独立取用（不丢任何一条腿）──
        vector_hits, vector_error = [], None
        if scope != "logs":
            vector_hits, vector_error = _vector_leg(dl, query, limit)
        log_hits, log_error = [], None
        if scope != "vector":
            log_hits, log_error = _log_leg(dl, query, limit)

        # ── 兼容字段 data：scope=all 时逐字节沿用原「搜索我的记忆」输出 ──
        if scope == "all":
            data = dl._combined_search(query, limit)
        elif scope == "vector":
            data = _format_vector_hits(query, vector_hits, limit)
        else:
            data = _format_log_hits(query, log_hits, limit)

        result = {
            "ok": True,
            "query": query,
            "scope": scope,
            # 两条腿条目数之和（不去重；data 内部按原文去重，口径见 YAML description）
            "count": len(vector_hits) + len(log_hits),
            "vector_hits": vector_hits,
            "log_hits": log_hits,
            "data": data,
        }
        if scope == "vector" and vector_error:
            # 对齐 expand_context 的失败语义：向量记忆不可用时 ok=False + error
            result.update({"ok": False, "error": vector_error, "count": 0})
        elif scope == "logs" and log_error:
            result.update({"ok": False, "error": log_error, "count": 0})
        else:
            # scope=all 保持原有 ok=True（另一条腿仍可能有结果），腿级故障单列字段披露
            if vector_error:
                result["vector_error"] = vector_error
            if log_error:
                result["log_error"] = log_error
        return result

    @_tools.register("remember", "记住重要信息，存储到长期记忆。后续可通过 search_memory 搜索到。important 级别会额外备份到桌面文件。", schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "记忆的标识名称，用于搜索定位"},
            "content": {"type": "string", "description": "要记住的具体内容"},
            "importance": {"type": "string", "enum": ["normal", "important"], "description": "重要程度，important 会额外备份到桌面永久记忆文件"},
        },
        "required": ["key", "content"],
    })
    def _remember(**kwargs):
        key = kwargs.get("key", "")
        content = kwargs.get("content", "")
        importance = kwargs.get("importance", "normal")
        if not key or not content:
            return {"ok": False, "error": "请提供 key 和 content 参数"}

        memory_text = f"[{key}] {content}"
        mem_id = None

        # 存到向量记忆
        if dl._vector_memory:
            try:
                mem_id = dl._vector_memory.add(
                    content=memory_text,
                    metadata={"type": "user_memory", "key": key, "importance": importance}
                )
            except Exception as e:
                logger.error("保存向量记忆失败: %s", e)

        # important 级别额外备份到桌面
        if importance == "important":
            try:
                backup_path = os.path.join(os.path.expanduser("~"), "Desktop", "云枢_永久记忆.md")
                with open(backup_path, "a", encoding="utf-8") as f:
                    f.write(f"\n## {key}\n{content}\n")
                backup_note = " + 桌面备份"
            except Exception as e:
                logger.error("桌面备份失败: %s", e)
                backup_note = ""
        else:
            backup_note = ""

        return {"ok": True, "data": f"✅ 已记住「{key}」{backup_note}", "mem_id": mem_id}

    @_tools.register("get_sensor_summary", "查看所有传感器状态", schema={
        "type": "object",
        "properties": {},
    })
    def _get_sensor_summary(**kwargs):
        return dl.body.get_sensor_summary()

    @_tools.register("search_lifetrace", "搜索我的记忆（使用 LifeTrace）", schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "required": ["query"],
    })
    def _search_lifetrace(**kwargs):
        if not dl._v2_lifetrace or not dl._memory_retriever:
            return {"ok": False, "error": "LifeTrace 系统未启用，此工具不可用", "available": False}
        query = kwargs.get("query", "")
        if not query:
            return {"ok": False, "error": "请提供搜索关键词"}
        try:
            results = dl._memory_retriever.retrieve(query, limit=10)
            if not results:
                return {"ok": True, "data": f"没有找到与 '{query}' 相关的记忆。", "count": 0}
            lines = "\n".join(
                f"- {node.content[:100]}"
                for node in results
            )
            return {"ok": True, "data": lines, "count": len(results)}
        except Exception as e:
            return {"ok": False, "error": f"搜索失败: {e}"}

    @_tools.register("get_persona_info", "查看当前人格配置", schema={
        "type": "object",
        "properties": {},
    })
    def _get_persona_info(**kwargs):
        if not dl._v2_persona or not dl._persona_model:
            return {"ok": False, "error": "Persona 系统未启用，此工具不可用", "available": False}
        identity = dl._persona_model.get_identity()
        style = dl._persona_model.get_expression_style()
        return {"ok": True, "data": {
            "identity": identity.get("identity"),
            "expression_style": style,
        }}

    @_tools.register("get_preferences", "查看学习到的用户偏好", schema={
        "type": "object",
        "properties": {},
    })
    def _get_preferences(**kwargs):
        report = dl.get_preferences_report()
        if not report or not report.get("enabled"):
            return {"ok": False, "error": "人格蒸馏功能未启用，此工具不可用", "available": False}
        prefs = report.get("preferences", {})
        lines = ["## 学习到的用户偏好\n"]

        if prefs.get("expression_style"):
            style = prefs["expression_style"]
            lines.append("### 表达风格偏好")
            for k, v in style.items():
                lines.append("- %s: %.2f" % (k, v))

        if prefs.get("topic_interest"):
            topics = sorted(prefs["topic_interest"].items(), key=lambda x: -x[1])[:5]
            lines.append("\n### 话题兴趣度")
            for topic, score in topics:
                lines.append("- %s: %.2f" % (topic, score))

        lines.append("\n最后更新: %s" % report.get('extracted_at', '未知'))
        return {"ok": True, "data": "\n".join(lines)}

    @_tools.register("trigger_distillation", "触发一次人格蒸馏学习", schema={
        "type": "object",
        "properties": {},
    })
    def _trigger_distillation(**kwargs):
        if not dl._v2_distillation:
            return {"ok": False, "error": "人格蒸馏功能未启用，此工具不可用", "available": False}
        dl._run_persona_distillation()
        return {"ok": True, "data": "人格蒸馏已触发！"}
