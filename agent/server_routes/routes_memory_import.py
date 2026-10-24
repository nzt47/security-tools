"""外部记忆导入 API（记忆管理页「导入外部记忆」的后端入口）。

分工与边界（与 agent/memory_import/service.py 的分工一致）：

| 端点 | 语义 | 写不写记忆 |
|---|---|---|
| GET  /api/memory-import/sources | 本机可解析的来源探测（只读目录存在性） | 不写 |
| POST /api/memory-import/preview | 采集 + 去噪/去重/脱敏/分批，落**计划快照** | 不写记忆 |
| GET  /api/memory-import/plans/<plan_id> | 回读预览快照（刷新页面后仍可确认） | 不写 |
| POST /api/memory-import/commit | 按批次写入；**默认 dry-run**，confirm=true 才真写并写审计 | 可能写 |

【不易】commit 的"是否真写"由后端判据决定（confirm=true 且 dry_run=false），
**不靠前端自觉**；前端把 confirm 做成勾选框只是二次确认的界面表达。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

from flask import request

from agent.api_envelope import ok, problem
from agent.memory_import import service as import_service
from agent.memory_import.models import MemoryImportError, SOURCES
from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

#: 单次 preview 的互斥锁（解析 51 MB 转录约 7-15s；并发触发只会互相拖慢）
_PREVIEW_LOCK = threading.Lock()

#: 来源的人读标签（与前端选项一一对应）
_SOURCE_LABELS: Dict[str, str] = {"claude": "Claude Code", "trae": "Trae"}

#: 预览响应里每个批次最多带几条样例（完整清单在计划快照里）
_PREVIEW_ITEMS = 5


def _parse_sources(raw: Any) -> List[str]:
    """规范化 sources：缺省全部；非法值直接 400（不静默丢弃）。"""
    if raw in (None, "", []):
        return list(SOURCES)
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raise ValueError("sources 必须是字符串或字符串数组")
    unknown = [name for name in candidates if name not in SOURCES]
    if unknown:
        raise ValueError("不支持的来源: " + ", ".join(unknown))
    return candidates or list(SOURCES)


def _public_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """裁掉逐文件细节与大段样例，只留确认批次需要的信息。"""
    batches = []
    for batch in plan.get("batches") or []:
        item = dict(batch)
        item["items"] = item.get("items", [])[:_PREVIEW_ITEMS]
        # record_ids 只服务快照与审计（一次可能上千个），界面只认条数
        item.pop("record_ids", None)
        batches.append(item)
    stats = plan.get("stats") or {}
    redaction = plan.get("redaction") or {}
    return {
        "plan_id": plan.get("plan_id"),
        "generated_at": plan.get("generated_at"),
        "plan_path": plan.get("plan_path"),
        "stats": stats,
        "redaction": redaction,
        "dropped": plan.get("dropped") or {},
        "batches": batches,
        "requires_confirm": True,
        "dry_run": True,
    }


def _error(exc: MemoryImportError):
    """域错误 -> RFC 9457（title/detail 分开，不泄露明文）。"""
    return problem(exc.status, title=exc.title, detail=exc.detail,
                   type_slug=exc.code)


def register_routes(app, state) -> None:
    """注册外部记忆导入路由（记忆管理页使用）。"""

    @app.route("/api/memory-import/sources", methods=["GET"])
    @trace_route("MemoryImport")
    @require_token
    @log_request(show_response=False)
    def api_memory_import_sources():
        """探测本机可解析的来源（只读；不解析内容，秒回）。"""
        import os
        from pathlib import Path

        from agent.memory_import.readers import claude as claude_reader
        from agent.memory_import.readers import trae as trae_reader

        items: List[Dict[str, Any]] = []
        claude_dir = claude_reader.default_claude_dir()
        items.append({
            "id": "claude",
            "label": _SOURCE_LABELS["claude"],
            "available": claude_dir.is_dir(),
            "detail": str(claude_dir),
            "has_transcripts": (claude_dir / "projects").is_dir(),
        })
        roots = trae_reader.find_trae_roots()
        items.append({
            "id": "trae",
            "label": _SOURCE_LABELS["trae"],
            "available": bool(roots),
            "detail": ", ".join(str(p) for p in roots) or str(
                Path(os.environ.get("APPDATA") or "") / "Trae CN"),
            "has_transcripts": False,  # 本地无会话正文（只有提问轨迹）
        })
        return ok({"sources": items,
                   "note": "Trae 的会话正文在云端，本地可解析的是提问轨迹"})

    @app.route("/api/memory-import/preview", methods=["POST"])
    @trace_route("MemoryImport")
    @require_token
    @log_request()
    def api_memory_import_preview():
        """只读预览：返回分批清单与统计（**不写任何记忆**）。"""
        body = request.get_json(silent=True) or {}
        try:
            sources = _parse_sources(body.get("sources"))
        except ValueError as exc:
            return problem(400, title="来源参数无效", detail=str(exc),
                           type_slug="invalid-source")
        include_transcripts = body.get("include_transcripts", True)
        if not _PREVIEW_LOCK.acquire(blocking=False):
            return problem(409, title="预览正在进行",
                           detail="已有一次预览在跑，请等它结束再试",
                           type_slug="preview-busy")
        try:
            plan = import_service.preview(
                sources=sources,
                include_transcripts=bool(include_transcripts),
                save=True,
            )
        except MemoryImportError as exc:
            return _error(exc)
        except Exception:  # noqa: BLE001 解析失败要如实报，但不把栈信息回给客户端
            # 【不易】detail 只给人读的下一步，不塞异常文本：异常可能带路径/键名，
            # 回显即"信息暴露"（CodeQL: information exposure through an exception）。
            logger.exception("外部记忆导入预览失败")
            return problem(500, title="预览失败",
                           detail="解析来源时出错，详见服务日志（本次未写入任何记忆）",
                           type_slug="preview-failed")
        finally:
            _PREVIEW_LOCK.release()
        return ok(_public_plan(plan))

    @app.route("/api/memory-import/commit", methods=["POST"])
    @trace_route("MemoryImport")
    @require_token
    @log_request()
    def api_memory_import_commit():
        """按批次写入：默认 dry-run；confirm=true 才真写（并写审计）。"""
        body = request.get_json(silent=True) or {}
        plan_id = str(body.get("plan_id") or "").strip()
        if not plan_id:
            return problem(400, title="缺少 plan_id",
                           detail="commit 必须基于一次 preview 的 plan_id",
                           type_slug="missing-plan-id")
        batch_ids = body.get("batch_ids") or body.get("batch") or []
        if isinstance(batch_ids, str):
            batch_ids = [part.strip() for part in batch_ids.split(",") if part.strip()]
        if not isinstance(batch_ids, (list, tuple)):
            return problem(400, title="批次参数无效",
                           detail="batch_ids 必须是字符串数组", type_slug="invalid-batch")
        dry_run = bool(body.get("dry_run", True))
        confirm = body.get("confirm") is True
        if not dry_run and not confirm:
            return problem(400, title="需要显式确认",
                           detail="真写入必须同时传 confirm=true（先看 preview 的批次）",
                           type_slug="confirmation-required")
        try:
            result = import_service.commit(
                plan_id, list(batch_ids) or None,
                confirm=confirm, dry_run=dry_run,
            )
        except MemoryImportError as exc:
            return _error(exc)
        except Exception:  # noqa: BLE001
            logger.exception("外部记忆导入提交失败")
            return problem(500, title="写入失败",
                           detail="写入过程中出错，详见服务日志；已成功的批次见批次清单",
                           type_slug="commit-failed")
        return ok(result)


__all__ = ["register_routes"]
