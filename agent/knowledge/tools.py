"""对话工具入口：知识工作流暴露为可调用工具（任务7 · Step 4）。

6 个工具：kb_capture / kb_distill / kb_discuss / kb_card / kb_lint / kb_search。
复用 agent.tools 注册机制（register / register_dynamic），提供
register_knowledge_tools() 显式注册（不侵入全局，由宿主按需调用）。

【不易】边界护栏（写死在工具实现，与 AGENTS.md 一致）：
- AI 禁止自动把「人生决策/个人日记」类内容写入 wiki——此类素材仅允许进入
  raw/inbox，且由 ingest 自动 detect_sensitive 标记（任务1），产卡层拦截。
- AI 只标记矛盾、建议归档，不自动裁决；kb_card 产卡结果恒为 draft，
  必须人工 transition → current 才转当前有效。
- 所有工具对 LLM 不可用降级（产出骨架产物），不抛异常；仅使用错误返回
  {"ok": False, "error": ...}。
"""
from __future__ import annotations

import functools
import logging
import os
import tempfile
import time
from pathlib import Path

from agent.knowledge.card import CardConflictError
from agent.knowledge.observability import emit_structured_log, knowledge_trace
from agent.knowledge.workflow import WorkflowRunner
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

_runner = None  # 惰性单例（同一进程复用同一 knowledge root）


def _get_runner() -> WorkflowRunner:
    """惰性构造 WorkflowRunner（knowledge root 由 env KNOWLEDGE_ROOT 决定）。"""
    global _runner
    if _runner is None:
        _runner = WorkflowRunner(llm=None)
        logger.info(log_dict({'module_name': 'tools', 'action': 'tools', 'msg': "[tools] 初始化 WorkflowRunner root=%s（KNOWLEDGE_ROOT=%s）" % (_runner.root,
                    os.environ.get("KNOWLEDGE_ROOT") or "(未设置，走仓库默认)")}))
    return _runner


def _trace_wrap(fn):
    """工具入口包装：一次工具调用 = 一条知识操作链路。

    同一 kb_* 调用内的所有 emit_structured_log 共享 trace_id（ContextVar 传播），
    分布式场景下可在 ELK 按 trace_id 聚合单次操作的全链路日志。
    """

    @functools.wraps(fn)
    def wrapper(**kw):
        with knowledge_trace():
            return fn(**kw)

    return wrapper


# ════════════════════════════════════════════════════════════
#  工具实现（handler 签名：**kwargs，返回 dict）
# ════════════════════════════════════════════════════════════

@_trace_wrap
def kb_capture(**kw) -> dict:
    """收集素材入库（Step1）：text 或 file_path → inbox/（敏感自动标记）。"""
    text = kw.get("text")
    file_path = kw.get("file_path")
    source_type = kw.get("source_type")
    runner = _get_runner()
    tmp = None
    try:
        if file_path:
            src = Path(file_path)
            logger.info(log_dict({'module_name': 'tools', 'action': 'kb_capture', 'msg': "[kb_capture] 请求: file_path=%s source_type=%s" % (file_path, source_type)}))
            if not src.is_file():
                logger.warning(log_dict({'module_name': 'tools', 'action': 'kb_capture', 'msg': "[kb_capture] 文件不存在: %s" % file_path}))
                return {"ok": False, "error": f"文件不存在: {file_path}"}
        elif text:
            fd, tmp = tempfile.mkstemp(suffix=".md", prefix="kb_capture_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            src = Path(tmp)
            logger.info(log_dict({'module_name': 'tools', 'action': 'kb_capture', 'msg': "[kb_capture] 请求: text 写入临时文件 %s（%d 字符）" % (tmp, len(text))}))
        else:
            logger.warning(log_dict({'module_name': 'tools', 'action': 'kb_capture', 'msg': "[kb_capture] 参数缺失: text 与 file_path 均为空"}))
            return {"ok": False, "error": "须提供 text 或 file_path 之一"}
        slug = runner.run_ingest(src, dest_layer="inbox", source_type=source_type)
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_capture.success', 'msg': "[kb_capture] 入库成功 slug=%s → inbox/%s.md（敏感由 ingest 自动标记）" % (slug, slug)}))
        return {"ok": True, "slug": slug, "hint": "已入库 inbox，可继续 kb_distill"}
    except Exception as exc:
        logger.error(log_dict({'module_name': 'tools', 'action': 'kb_capture.failed', 'msg': "[kb_capture] 入库失败: %s" % exc}), exc_info=True)
        return {"ok": False, "error": str(exc)}
    finally:
        if tmp is not None:
            os.unlink(tmp)


@_trace_wrap
def kb_distill(**kw) -> dict:
    """提炼结构化笔记（Step2）：source_path → processed/ 笔记（LLM 离线自动降级）。"""
    source_path = kw.get("source_path")
    if not source_path:
        return {"ok": False, "error": "须提供 source_path（inbox/raw 下素材路径）"}
    runner = _get_runner()
    _t0 = time.perf_counter()
    try:
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_distill', 'msg': "[kb_distill] 提炼请求 source=%s" % source_path}))
        note = runner.run_distill(source_path)
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_distill.success', 'msg': "[kb_distill] 提炼完成 slug=%s distilled=%s model=%s reason=%s" % (note.slug, note.distilled, note.llm_model or "none",
                    note.reason or "none")}))
        emit_structured_log("kb_distill.done", duration_ms=(time.perf_counter() - _t0) * 1000,
                            slug=note.slug, distilled=note.distilled,
                            model=note.llm_model or "none", reason=note.reason or "none")
        return {
            "ok": True,
            "slug": note.slug,
            "distilled": note.distilled,
            "reason": note.reason or "",
            "hint": "笔记已落 processed/，可 approve 后 kb_card 产卡",
        }
    except FileNotFoundError as exc:
        emit_structured_log("kb_distill.missing", level="warning",
                            duration_ms=(time.perf_counter() - _t0) * 1000,
                            source=source_path, error=str(exc))
        return {"ok": False, "error": str(exc)}


@_trace_wrap
def kb_discuss(**kw) -> dict:
    """深度讨论（Step3）：围绕笔记发起讨论，返回讨论记录路径。"""
    note_slug = kw.get("note_slug")
    question = kw.get("question") or ""
    if not note_slug:
        return {"ok": False, "error": "须提供 note_slug"}
    runner = _get_runner()
    try:
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_discuss', 'msg': "[kb_discuss] 讨论请求 note_slug=%s question=%r" % (note_slug, question)}))
        path = runner.run_discuss(note_slug, question)
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_discuss.success', 'msg': "[kb_discuss] 讨论完成 note_slug=%s → %s" % (note_slug, path)}))
        return {"ok": True, "discussion_path": path,
                "hint": "讨论记录已生成，可 kb_card（discussion_path）产卡"}
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}


@_trace_wrap
def kb_card(**kw) -> dict:
    """产卡（Step4）：note_slug（已 approve 笔记）或 discussion_path → wiki/ 卡片。

    人机边界：产物状态恒为 draft，须人工 transition → current。
    """
    note_slug = kw.get("note_slug")
    discussion_path = kw.get("discussion_path")
    card_type = kw.get("card_type") or "concepts"
    runner = _get_runner()
    _t0 = time.perf_counter()
    try:
        if discussion_path:
            mode = "discussion"
            logger.info(log_dict({'module_name': 'tools', 'action': 'kb_card', 'msg': "[kb_card] 产卡请求 mode=discussion path=%s card_type=%s" % (discussion_path, card_type)}))
            slug = runner.card_from_discussion(discussion_path, card_type)
        elif note_slug:
            mode = "note"
            logger.info(log_dict({'module_name': 'tools', 'action': 'kb_card', 'msg': "[kb_card] 产卡请求 mode=note note_slug=%s card_type=%s" % (note_slug, card_type)}))
            slug = runner.run_card(note_slug, card_type)
        else:
            logger.warning(log_dict({'module_name': 'tools', 'action': 'kb_card', 'msg': "[kb_card] 参数缺失: note_slug 与 discussion_path 均为空"}))
            return {"ok": False, "error": "须提供 note_slug 或 discussion_path 之一"}
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_card.success', 'msg': "[kb_card] 产卡成功 slug=%s（draft，待人工确认）" % slug}))
        emit_structured_log("kb_card.ok", duration_ms=(time.perf_counter() - _t0) * 1000,
                            slug=slug, mode=mode)
        return {"ok": True, "slug": slug, "status": "draft",
                "hint": "产卡成功（draft），须人工 transition 转 current"}
    except (FileNotFoundError, ValueError, CardConflictError) as exc:
        emit_structured_log("kb_card.failed", level="warning",
                            duration_ms=(time.perf_counter() - _t0) * 1000,
                            mode=mode, error=str(exc))
        return {"ok": False, "error": str(exc)}


@_trace_wrap
def kb_lint(**kw) -> dict:
    """健康巡检（Step5）：断链 + 孤儿检测，返回健康报告。"""
    try:
        report = _get_runner().run_audit()
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_lint.success', 'msg': "[kb_lint] 审计完成 卡片=%s 断链=%s 孤儿=%s ok=%s" % (report["total_cards"], len(report["broken_links"]),
                    report["orphans"], report["ok"])}))
        return {"ok": True, "report": report}
    except Exception as exc:
        logger.error(log_dict({'module_name': 'tools', 'action': 'kb_lint.failed', 'msg': "[kb_lint] 审计失败: %s" % exc}), exc_info=True)
        return {"ok": False, "error": str(exc)}


@_trace_wrap
def kb_search(**kw) -> dict:
    """知识检索：query → 融合检索命中（任务4 KnowledgeSearch）。"""
    query = kw.get("query")
    top_k = int(kw.get("top_k") or 5)
    if not query:
        return {"ok": False, "error": "须提供 query"}
    try:
        from agent.knowledge.card import CardStore
        from agent.knowledge.search import KnowledgeSearch

        runner = _get_runner()
        searcher = KnowledgeSearch(CardStore(runner.root / "wiki"))
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_search', 'msg': "[kb_search] 检索请求 query=%r top_k=%s" % (query, top_k)}))
        hits = searcher.search(query, top_k=top_k)
        logger.info(log_dict({'module_name': 'tools', 'action': 'kb_search.success', 'msg': "[kb_search] 检索完成 命中=%d → %s" % (len(hits), [h.slug for h in hits])}))
        return {
            "ok": True,
            "hits": [
                {"slug": h.slug, "title": h.title, "status": h.status,
                 "type": h.type, "score": round(h.score, 3),
                 "source_ref": h.source_ref, "snippet": h.snippet}
                for h in hits
            ],
        }
    except Exception as exc:
        logger.error(log_dict({'module_name': 'tools', 'action': 'kb_search.failed', 'msg': "[kb_search] 检索失败: %s" % exc}))
        return {"ok": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════
#  注册入口
# ════════════════════════════════════════════════════════════

_TOOL_DEFS: list[tuple[str, str, dict]] = [
    ("kb_capture", "收集素材入库（text 或 file_path → inbox/，敏感自动标记）", {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "素材文本（与 file_path 二选一）"},
            "file_path": {"type": "string", "description": "素材文件路径（与 text 二选一）"},
            "source_type": {"type": "string", "description": "来源类型（文章/播客/会议…）"},
        },
    }),
    ("kb_distill", "提炼结构化笔记（source_path → processed/，LLM 离线自动降级）", {
        "type": "object",
        "properties": {
            "source_path": {"type": "string", "description": "inbox/raw 下素材路径"},
        },
    }),
    ("kb_discuss", "深度讨论（围绕笔记人机追问校准，返回讨论记录路径）", {
        "type": "object",
        "properties": {
            "note_slug": {"type": "string", "description": "processed/ 笔记 slug"},
            "question": {"type": "string", "description": "讨论问题"},
        },
    }),
    ("kb_card", "产卡（已确认笔记或讨论记录 → wiki/ 卡片，产物恒为 draft）", {
        "type": "object",
        "properties": {
            "note_slug": {"type": "string", "description": "已 approve 的笔记 slug"},
            "discussion_path": {"type": "string", "description": "讨论记录路径"},
            "card_type": {"type": "string",
                          "enum": ["concepts", "entities", "insights"],
                          "description": "卡片类型（默认 concepts）"},
        },
    }),
    ("kb_lint", "健康巡检（断链 + 孤儿检测，返回知识库健康报告）", {
        "type": "object",
        "properties": {},
    }),
    ("kb_search", "知识检索（语义融合检索知识卡片）", {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题/关键词"},
            "top_k": {"type": "integer", "description": "返回条数（默认 5）"},
        },
        "required": ["query"],
    }),
]

_KB_HANDLERS = {
    "kb_capture": kb_capture,
    "kb_distill": kb_distill,
    "kb_discuss": kb_discuss,
    "kb_card": kb_card,
    "kb_lint": kb_lint,
    "kb_search": kb_search,
}


def register_knowledge_tools(clear_first: bool = True) -> int:
    """注册 6 个知识工具到 agent.tools 全局注册表。

    Args:
        clear_first: 是否先注销同名旧注册（重复注册/热重载时幂等）。
    Returns:
        实际注册的工具数。
    """
    from agent.tools import register, unregister

    if clear_first:
        for name, _, _ in _TOOL_DEFS:
            unregister(name)
    for name, description, schema in _TOOL_DEFS:
        register(name, description, schema=schema, handler=_KB_HANDLERS[name])
    logger.info(log_dict({'module_name': 'tools', 'action': 'tools.success', 'msg': "[tools] 知识工具注册完成 %d 个: %s" % (len(_TOOL_DEFS),
                ", ".join(n for n, _, _ in _TOOL_DEFS))}))
    return len(_TOOL_DEFS)


def unregister_knowledge_tools() -> int:
    """注销全部知识工具（测试隔离用）。"""
    from agent.tools import unregister

    for name, _, _ in _TOOL_DEFS:
        unregister(name)
    return len(_TOOL_DEFS)
