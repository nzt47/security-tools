"""知识库检索 API 路由（任务4：知识检索整合）。

组合接线存量检索能力（【不易】：不修改 tool_router_hybrid / tool_router_reranker）：
    CardStore（卡片事实源）→ KnowledgeSearch（RRF 融合 + 双链扩展 + rerank 重排）
    + VectorStore（语义向量，可选）+ ToolReranker（精排，可选，降级原序）。

路由契约（任务4 Step 5）：
    POST /api/knowledge/query  {"question": "...", "top_k": 5}
    → {"ok": true, "hits": [{slug/title/status/type/score/rerank_score/source_ref/snippet}]}
      "result" 字段保留旧调用方兼容（format_context 引用块）。
"""

import logging
import threading

from flask import jsonify, request

from agent.knowledge import KnowledgeSearch, format_context
from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册知识库检索路由。"""

    Yunshu = state.Yunshu
    _searcher = None  # 模块级懒加载单例（卡片/向量/精排接线一次完成）
    _searcher_lock = threading.Lock()

    def _get_searcher():
        """懒加载 KnowledgeSearch（鸭子类型接线，缺失逐级降级）。

        双重检查锁：Flask threaded 模式下并发首请求只构建一次检索器
        （BM25 全库索引是昂贵操作，重复构建浪费且无状态损坏）。
        """
        nonlocal _searcher
        if _searcher is not None:
            return _searcher
        with _searcher_lock:
            if _searcher is not None:  # 首个持锁线程构建期间，其余线程等待后直接复用
                return _searcher
            card_store = getattr(Yunshu, "_card_store", None)
            if card_store is None:
                logger.warning("知识库检索: Yunshu._card_store 未初始化，返回 503")
                return None
            vector_store = getattr(Yunshu, "_vector_memory", None)
            reranker = None
            try:
                from agent.tool_router_reranker import get_tool_reranker
                reranker = get_tool_reranker()  # 环境开关未启用/不可用 → None（降级）
            except Exception as exc:
                logger.warning("知识库检索: reranker 获取失败，降级 RRF 原序: %s", exc)
            _searcher = KnowledgeSearch(
                card_store, vector_store=vector_store, reranker=reranker
            )
            return _searcher

    @app.route("/api/knowledge/query", methods=["POST"])
    @trace_route("Knowledge")
    @require_token
    @log_request()
    def api_knowledge_query():
        """知识库融合检索：RRF 融合 + 双链扩展 + rerank 重排。"""
        data = request.get_json() or {}
        question = data.get("question", "").strip()
        try:
            top_k = max(1, min(int(data.get("top_k", 5)), 20))
        except (TypeError, ValueError):
            top_k = 5

        if not question:
            return jsonify({"ok": False, "error": "查询问题不能为空"}), 400

        searcher = _get_searcher()
        if searcher is None:
            return jsonify({"ok": False, "error": "知识库未初始化"}), 503

        try:
            hits = searcher.search(question, top_k=top_k)
            return jsonify({
                "ok": True,
                "hits": [
                    {
                        "slug": h.slug,
                        "title": h.title,
                        "status": h.status,
                        "type": h.type,
                        "score": h.score,
                        "rerank_score": h.rerank_score,
                        "source_ref": h.source_ref,
                        "snippet": h.snippet,
                    }
                    for h in hits
                ],
                # 兼容旧调用方：format_context 引用块
                "result": format_context(hits, top_k=top_k),
            })
        except Exception as e:
            logger.error("知识库检索失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
