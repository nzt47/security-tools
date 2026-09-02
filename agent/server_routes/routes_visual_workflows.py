"""可视化编辑器工作流草稿持久化 API

背景：工作台"记忆管理 → 可视化编辑"（VisualEditor）手工编排的 workflow 图
（nodes/edges + 生成的 YAML 文本）需要保存/加载闭环。既有 workflow-learning
的存储（learned_workflows.json）服务于"从 LLM 工具调用学习 + 匹配执行"，
其数据模型（WorkflowStep.tool_name / matcher 索引）与可视化编排图不同，
直接复用会破坏既有语义。因此本模块提供一套**独立且最小**的草稿存储：

- 数据文件：data/visual_workflows.json（与 learned_workflows.json 完全隔离）
- 不做任何匹配/执行/转换逻辑，仅 JSON 原样存取（写入前做字段规范化）
- 新增端点全部位于 /api/visual-workflows/*，不影响任何既有路由

能力说明（供前端展示）：
- GET    /api/visual-workflows            → 草稿列表（摘要）
- GET    /api/visual-workflows/<vid>      → 单个草稿（含 nodes/edges/yaml）
- POST   /api/visual-workflows            → 保存/更新（按 id upsert；无 id 自动生成）
- DELETE /api/visual-workflows/<vid>      → 删除
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import jsonify, request

from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

# 仓库文件：data/visual_workflows.json（可经环境变量 VISUAL_WORKFLOWS_STORE 覆盖，测试用）
DEFAULT_STORE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "visual_workflows.json"
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_LOCK = threading.RLock()


def _store_path() -> Path:
    override = os.environ.get("VISUAL_WORKFLOWS_STORE")
    return Path(override) if override else DEFAULT_STORE_PATH


# ─── 纯文件存储（与既有 WorkflowRepository 同款原子写模式）───────────────

def _read_all(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[VisualWorkflows] 仓库读取失败(%s)，按空仓库处理: %s", path, e)
        return {}


def _write_all(path: Path, data: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ─── 记录构造/规范化 ───────────────────────────────────────────────────

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _slugify(name: str) -> str:
    """把中文/空格等名称转成合法 id；不合法时返回空串（由调用方兜底）。"""
    slug = re.sub(r"[^a-z0-9_\-]+", "-", (name or "").strip().lower()).strip("-")
    if not slug or not _ID_RE.match(slug):
        return ""
    return slug[:128]


def _clean_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):  # NaN/Inf 过滤
            return default
        return num
    except (TypeError, ValueError):
        return default


def _clean_node(raw: Any, index: int) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    node_id = raw.get("id")
    if not node_id:
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        data = {}
    pos = raw.get("position")
    position = {"x": 0.0, "y": 0.0}
    if isinstance(pos, dict):
        position = {
            "x": _clean_float(pos.get("x")),
            "y": _clean_float(pos.get("y")),
        }
    node_type = str(raw.get("type") or data.get("nodeType") or "skill")
    # 只保留平面 JSON 安全字段，丢弃 xyflow 运行时附加的 measured/internals 等
    return {
        "id": str(node_id),
        "type": node_type,
        "position": position,
        "data": {
            "label": str(data.get("label") or f"节点{index + 1}"),
            "nodeType": str(data.get("nodeType") or node_type),
            **{k: v for k, v in data.items() if k not in ("label", "nodeType")},
        },
    }


def _clean_edge(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    edge_id = raw.get("id")
    source = raw.get("source")
    target = raw.get("target")
    if not edge_id or not source or not target:
        return None
    out: Dict[str, Any] = {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
    }
    # 条件分支区分 true/false sourceHandle
    if raw.get("sourceHandle") is not None:
        out["sourceHandle"] = str(raw["sourceHandle"])
    if raw.get("targetHandle") is not None:
        out["targetHandle"] = str(raw["targetHandle"])
    return out


def _build_record(payload: Dict[str, Any], existing: Optional[dict]) -> Optional[dict]:
    """规范化保存请求为草稿记录；字段非法返回 None。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        name = "未命名工作流"

    nodes_raw = payload.get("nodes")
    edges_raw = payload.get("edges")
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        return None

    nodes = [n for n in (_clean_node(x, i) for i, x in enumerate(nodes_raw)) if n]
    edges = [e for e in (_clean_edge(x) for x in edges_raw) if e]

    # id：显式给定（校验合法）→ 复用；否则用名称 slug，冲突或为空则时间戳兜底
    vid = str(payload.get("id") or "").strip()
    if not (_ID_RE.match(vid) and len(vid) <= 128):
        vid = _slugify(name)
    if not vid:
        vid = f"visual_{int(time.time() * 1000)}"
    if len(vid) > 128:
        vid = vid[:128]

    now = _now()
    yaml_text = payload.get("yaml")
    record = {
        "id": vid,
        "name": name[:200],
        "description": str(payload.get("description") or "")[:500],
        "nodes": nodes,
        "edges": edges,
        "yaml": str(yaml_text) if yaml_text is not None else "",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    return record


def _summary(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "description": record.get("description", ""),
        "node_count": record.get("node_count", 0),
        "edge_count": record.get("edge_count", 0),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


# ─── 路由 ─────────────────────────────────────────────────────────────

def register_routes(app, state) -> None:
    """注册可视化工作流草稿路由"""

    @app.route("/api/visual-workflows", methods=["GET"])
    @trace_route("VisualWorkflows")
    @log_request(show_response=False)
    def api_visual_list():
        try:
            path = _store_path()
            with _LOCK:
                items = [_summary(v) for v in _read_all(path).values()]
            items.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
            return jsonify({"ok": True, "items": items, "total": len(items)})
        except Exception as e:  # noqa: BLE001
            logger.error("[VisualWorkflows] 列表失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/visual-workflows/<vid>", methods=["GET"])
    @trace_route("VisualWorkflows")
    @log_request(show_response=False)
    def api_visual_get(vid: str):
        try:
            path = _store_path()
            with _LOCK:
                record = _read_all(path).get(vid)
            if not record:
                return jsonify({"ok": False, "error": "草稿不存在"}), 404
            return jsonify({"ok": True, "workflow": record})
        except Exception as e:  # noqa: BLE001
            logger.error("[VisualWorkflows] 读取失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/visual-workflows", methods=["POST"])
    @trace_route("VisualWorkflows")
    @require_token
    @log_request(show_response=False)
    def api_visual_save():
        """保存（upsert）可视化工作流草稿

        Body: {
            id?: string,           # 更新已有草稿时传入；缺省按 name 生成
            name: string,
            description?: string,
            nodes: [{id, type, position:{x,y}, data:{...}}],
            edges: [{id, source, target, sourceHandle?, targetHandle?}],
            yaml?: string          # VisualEditor 生成的 YAML 文本（可选，便于审计）
        }
        """
        try:
            payload = request.get_json(silent=True) or {}
            path = _store_path()
            with _LOCK:
                store = _read_all(path)
                existing = store.get(str(payload.get("id") or ""))
                record = _build_record(payload, existing)
                if not record:
                    return jsonify({
                        "ok": False,
                        "error": "nodes/edges 必须为非空数组",
                        "code": "VALIDATION_ERROR",
                    }), 400
                store[record["id"]] = record
                _write_all(path, store)
            logger.info(
                "[VisualWorkflows] 已保存草稿 %s（%d 节点 / %d 连线）",
                record["id"], record["node_count"], record["edge_count"],
            )
            return jsonify({"ok": True, "workflow": _summary(record),
                            "action": "updated" if existing else "created"})
        except Exception as e:  # noqa: BLE001
            logger.error("[VisualWorkflows] 保存失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/visual-workflows/<vid>", methods=["DELETE"])
    @trace_route("VisualWorkflows")
    @require_token
    @log_request(show_response=False)
    def api_visual_delete(vid: str):
        try:
            path = _store_path()
            with _LOCK:
                store = _read_all(path)
                if vid not in store:
                    return jsonify({"ok": False, "error": "草稿不存在"}), 404
                del store[vid]
                _write_all(path, store)
            logger.info("[VisualWorkflows] 已删除草稿 %s", vid)
            return jsonify({"ok": True})
        except Exception as e:  # noqa: BLE001
            logger.error("[VisualWorkflows] 删除失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
