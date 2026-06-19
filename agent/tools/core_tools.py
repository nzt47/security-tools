"""工具注册模块 — 核心工具（状态、记忆、感知、人格）"""
import logging
import os
from agent import tools as _tools

logger = logging.getLogger(__name__)


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

    @_tools.register("search_memory", "搜索我的记忆", schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "required": ["query"],
    })
    def _search_memory(**kwargs):
        query = kwargs.get("query", "")
        if not query:
            return {"ok": False, "error": "请提供搜索关键词"}
        result = dl._combined_search(query)
        return {"ok": True, "data": result}

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

    @_tools.register("expand_context",
                    "从记忆库中查找更多与当前话题相关的上下文信息。当你觉得当前对话缺少关键信息时调用此工具。",
                    schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词或问题描述"},
                            "max_items": {"type": "integer", "description": "最多返回条目数，默认 5"},
                        },
                        "required": ["query"],
                    })
    def _expand_context(**kwargs):
        from agent.system_tools import expand_context_from_memory
        query = kwargs.get("query", "")
        max_items = kwargs.get("max_items", 5)
        return expand_context_from_memory(dl, query, max_items)
