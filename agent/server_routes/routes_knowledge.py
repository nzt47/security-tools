"""知识库 API 路由（任务4 + 任务6）。

组合接线存量检索能力（【不易】：不修改 tool_router_hybrid / tool_router_reranker）：
    CardStore（卡片事实源）→ KnowledgeSearch（RRF 融合 + 双链扩展 + rerank 重排）
    + VectorStore（语义向量，可选）+ ToolReranker（精排，可选，降级原序）。

路由契约（任务4 Step 5 + 任务6 Step 1）：
    POST   /api/knowledge/query         融合检索（任务4）
    GET    /api/knowledge/cards         卡片列表（?status=&type= 过滤）
    GET    /api/knowledge/cards/<slug>  卡片详情（含 links、contradictions、incoming_links）
    POST   /api/knowledge/cards         创建卡片（body 为任务0 Card dict）
    PATCH  /api/knowledge/cards/<slug>  更新卡片（支持状态迁移 transition）
    DELETE /api/knowledge/cards/<slug>  删除卡片（有入链时 409 返回原因）
    GET    /api/knowledge/index         index.md 内容
    GET    /api/knowledge/lint          健康报告（lint_all）
    GET    /api/knowledge/graph         节点-边数据

错误处理约定（与现有路由一致，均返回 JSON 不抛 HTML）：
    不存在 slug → 404 {"error": "..."}
    schema 校验失败 → 422 {"error": "...", "violations": [...]}
    有入链删除 / 非法状态迁移 → 409（删除场景含 incoming_links 列表）
"""

import dataclasses
import logging
import threading
from dataclasses import asdict

from flask import jsonify, request

from agent.knowledge import (
    Card, CardConflictError, CardNotFoundError, InvalidTransitionError,
    KnowledgeSearch, format_context, validate_card,
)
from agent.knowledge.links_index import read_links_index
from agent.knowledge.lint import lint_all
from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def _card_to_dict(card: Card) -> dict:
    """Card → API 响应 dict（剔除仅内存标记 explicit_slug）。"""
    d = asdict(card)
    d.pop("explicit_slug", None)
    return d


def _make_card(data: dict) -> Card:
    """API body dict → Card。

    未知字段忽略；缺失字段补默认值（必填字段补空串），使卡片总能构造，
    让 validate_card 产出「缺少必填字段」违规项（PATCH 部分更新只拷贝
    非空字段，空串默认值不会覆盖既有值）。
    """
    fields = Card.__dataclass_fields__
    kwargs = {k: v for k, v in data.items() if k in fields}
    for name, f in fields.items():
        if name in kwargs:
            continue
        if f.default is not dataclasses.MISSING:
            kwargs[name] = f.default
        elif f.default_factory is not dataclasses.MISSING:
            kwargs[name] = f.default_factory()
        else:
            kwargs[name] = ""
    return Card(**kwargs)


def register_routes(app, state):
    """注册知识库路由（检索 + CRUD + index + lint + graph）。"""

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

    # ═══════════════════════════════════════════════════════════
    #  知识库 CRUD + index + lint + graph（任务6）
    # ═══════════════════════════════════════════════════════════

    def _get_store():
        """获取 CardStore；未初始化返回 None（路由层统一 503）。"""
        card_store = getattr(Yunshu, "_card_store", None)
        if card_store is None:
            logger.warning("知识库: Yunshu._card_store 未初始化，返回 503")
        return card_store

    def _incoming_links(store, slug: str) -> list[str]:
        """计算指向 slug 的入链（引用方 slug 列表）。

        优先读入链索引文件（O(1) 查表）；索引缺失/解析失败时回退全库扫描
        （与 CardStore._has_incoming_links 降级语义一致）。
        """
        try:
            idx = read_links_index(store._links_index_path)
            if idx:
                return idx.get(slug, [])
        except (ValueError, OSError) as exc:
            logger.warning("知识库: 入链索引解析失败，回退全库扫描: %s", exc)
        return [
            c.slug for c in store.list(use_cache=True)
            if c.slug != slug and slug in c.links
        ]

    def _store_required():
        """统一 503 守卫：CardStore 未初始化。"""
        store = _get_store()
        if store is None:
            return None, (jsonify({"ok": False, "error": "知识库未初始化"}), 503)
        return store, None

    @app.route("/api/knowledge/cards", methods=["GET"])
    @trace_route("Knowledge")
    @require_token
    @log_request(show_response=False)
    def api_knowledge_list_cards():
        """卡片列表（支持 ?status= & ?type= 过滤）。"""
        store, err = _store_required()
        if err:
            return err
        status = request.args.get("status") or None
        type_ = request.args.get("type") or None
        try:
            cards = store.list(status=status, type=type_, use_cache=True)
            return jsonify({
                "ok": True,
                "cards": [_card_to_dict(c) for c in cards],
                "count": len(cards),
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 422

    @app.route("/api/knowledge/cards/<slug>", methods=["GET"])
    @trace_route("Knowledge")
    @require_token
    @log_request(show_response=False)
    def api_knowledge_get_card(slug):
        """卡片详情（含 links、contradictions、incoming_links）。"""
        store, err = _store_required()
        if err:
            return err
        card = store.get(slug)
        if card is None:
            return jsonify({"ok": False, "error": f"卡片不存在: {slug}"}), 404
        data = _card_to_dict(card)
        data["incoming_links"] = _incoming_links(store, slug)
        return jsonify({"ok": True, "card": data})

    @app.route("/api/knowledge/cards", methods=["POST"])
    @trace_route("Knowledge")
    @require_token
    @log_request()
    def api_knowledge_create_card():
        """创建卡片（body 为任务0 Card dict）。"""
        store, err = _store_required()
        if err:
            return err
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400
        card = _make_card(data)
        violations = validate_card(asdict(card))
        if violations:
            return jsonify({
                "ok": False,
                "error": "卡片 schema 校验失败",
                "violations": violations,
            }), 422
        try:
            created = store.create(card)
        except CardConflictError as e:
            logger.warning("知识库: 创建冲突 slug=%s: %s", card.slug, e)
            return jsonify({"ok": False, "error": str(e)}), 409
        except Exception as e:
            logger.error("知识库: 创建卡片异常: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "card": _card_to_dict(created)}), 201

    @app.route("/api/knowledge/cards/<slug>", methods=["PATCH"])
    @trace_route("Knowledge")
    @require_token
    @log_request()
    def api_knowledge_update_card(slug):
        """更新卡片；body 可含 transition（状态迁移）或部分字段更新。"""
        store, err = _store_required()
        if err:
            return err
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400

        if "transition" in data:
            to_status = data.get("transition")
            # 允许 transition 与字段更新同时出现：先更新字段再迁移状态
            if {k for k in data if k != "transition"}:
                base = store.get(slug)
                if base is None:
                    return jsonify({"ok": False, "error": f"卡片不存在: {slug}"}), 404
                patch = _make_card({k: v for k, v in data.items() if k != "transition"})
                for f in Card.__dataclass_fields__:
                    if getattr(patch, f, None) not in (None, "", [], {}):
                        setattr(base, f, getattr(patch, f))
                violations = validate_card(asdict(base))
                if violations:
                    return jsonify({
                        "ok": False, "error": "卡片 schema 校验失败",
                        "violations": violations,
                    }), 422
                store.update(base)
            try:
                card = store.transition(slug, to_status)
            except CardNotFoundError as e:
                return jsonify({"ok": False, "error": str(e)}), 404
            except InvalidTransitionError as e:
                logger.warning("知识库: 非法状态迁移 slug=%s: %s", slug, e)
                return jsonify({"ok": False, "error": str(e)}), 409
            return jsonify({"ok": True, "card": _card_to_dict(card)})

        base = store.get(slug)
        if base is None:
            return jsonify({"ok": False, "error": f"卡片不存在: {slug}"}), 404
        patch = _make_card(data)
        for f in Card.__dataclass_fields__:
            if f == "slug":
                continue  # slug 不可变（定位标识）
            if getattr(patch, f, None) not in (None, "", [], {}):
                setattr(base, f, getattr(patch, f))
        violations = validate_card(asdict(base))
        if violations:
            return jsonify({
                "ok": False, "error": "卡片 schema 校验失败",
                "violations": violations,
            }), 422
        try:
            updated = store.update(base)
        except Exception as e:
            logger.warning("知识库: 更新卡片失败 slug=%s: %s", slug, e)
            return jsonify({"ok": False, "error": str(e)}), 409
        return jsonify({"ok": True, "card": _card_to_dict(updated)})

    @app.route("/api/knowledge/cards/<slug>", methods=["DELETE"])
    @trace_route("Knowledge")
    @require_token
    @log_request()
    def api_knowledge_delete_card(slug):
        """删除卡片；有入链时 409 并返回入链列表。"""
        store, err = _store_required()
        if err:
            return err
        if store.get(slug) is None:
            return jsonify({"ok": False, "error": f"卡片不存在: {slug}"}), 404
        deleted = store.delete(slug)
        if not deleted:
            incoming = _incoming_links(store, slug)
            return jsonify({
                "ok": False,
                "error": f"卡片存在入链，删除被拒: {slug}",
                "incoming_links": incoming,
            }), 409
        return jsonify({"ok": True, "deleted": slug})

    @app.route("/api/knowledge/index", methods=["GET"])
    @trace_route("Knowledge")
    @require_token
    @log_request(show_response=False)
    def api_knowledge_index():
        """获取 index.md 内容。"""
        store, err = _store_required()
        if err:
            return err
        index_path = store._index_path
        if not index_path.exists():
            return jsonify({"ok": False, "error": "index.md 不存在"}), 404
        return jsonify({"ok": True, "content": index_path.read_text(encoding="utf-8")})

    @app.route("/api/knowledge/lint", methods=["GET"])
    @trace_route("Knowledge")
    @require_token
    @log_request(show_response=False)
    def api_knowledge_lint():
        """健康报告（lint_all）。"""
        store, err = _store_required()
        if err:
            return err
        try:
            report = lint_all(store, index_path=store._index_path)
            return jsonify({"ok": True, "report": asdict(report)})
        except Exception as e:
            logger.error("知识库: lint 巡检失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/knowledge/graph", methods=["GET"])
    @trace_route("Knowledge")
    @require_token
    @log_request(show_response=False)
    def api_knowledge_graph():
        """节点-边数据（关系图入口）。"""
        store, err = _store_required()
        if err:
            return err
        cards = store.list(use_cache=True)
        nodes = [
            {"id": c.slug, "label": c.title, "type": c.type, "status": c.status}
            for c in cards
        ]
        # 只保留指向 wiki 节点的纯 slug 边（过滤 archives/ 目标）
        wiki_slugs = {c.slug for c in cards}
        edges = [
            {"source": c.slug, "target": link}
            for c in cards
            for link in c.links
            if link in wiki_slugs
        ]
        return jsonify({"ok": True, "nodes": nodes, "edges": edges})
