# plugins/experience.py
"""经验库插件（方案「把 DSH 的历史会话提炼成经验库」P3）。

职责：把 P1 产出的经验样本（samples.ndjson）以只读+审阅的方式暴露给 UI，
并提供 ingest（仅元数据+diff）/审阅/批量回滚/统计/检索 七类接口。

【与方案的偏差（P0 实测结论，已在方案「二·补」节记录）】
  1. 方案要求「经验作为 Skill 接入 agent/capregistry/」—— 实测不可行：
     capregistry 是构建期只读派生视图（spec.py:28-29 无写入方法、
     view.py:36-41 无写方法），数据源是受 CI 守门的生成物。故本插件
     走独立的运行期存储，不注册进 capregistry。
  2. 方案要求 review 走 L2、rollback 走 L3 —— 实测技能/审批域的值域只有
     L0/L1/L2，**不存在 L3**（agent/skills_mgmt/approval.py:71）。故：
       - review 用 L2 语义（请求体须显式 confirmed=true，单次有效）；
       - rollback 用 L2 + **审计链留痕**（agent.audit.facade）作为最强可得保障。

【安全】所有变更型接口均带 @require_token（从 agent.server_auth 取完整版，
含令牌映射与身份绑定）。这与 P0 审计发现的「74/82 条无鉴权」问题同源 ——
本插件不重复该错误。

【模块级副作用】仅 Blueprint / 常量 / 函数定义（loader reload 安全）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from agent.server_auth import require_token
from .plugin_api import Plugin, register_plugin

bp = Blueprint("experience", __name__)

# ── 存储路径（可用环境变量覆盖；默认与 P1 extract.py 产出对齐）──
_BASE = os.environ.get("CP_EXPERIENCE_DIR", os.path.join("data", "experience"))
#: 检索相关性下限（BM25 原始分）。默认取自 eval/sweep_threshold.py 实测的可分间隔中位。
#: 设 0 表示关闭判定（任何查询都返回 top-K）—— 仅调试用，见 experience_index 的常量注释。
_MIN_SCORE = float(os.environ.get("CP_EXPERIENCE_MIN_SCORE", "30") or 30)
_CORPUS = os.path.join(_BASE, "samples.ndjson")
_REVIEWS = os.path.join(_BASE, "reviews.jsonl")
_BATCHES = os.path.join(_BASE, "batches.jsonl")
_LOCK = threading.Lock()

#: 硬阻断模式（P0 假阳性审计后的定稿集；身份证号与 AWS AKIA 经实测剔除）
_HARD_BLOCK = {
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "api_key_sk": re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
    "slack_webhook": re.compile(r"https://hooks\.slack\.com/\S+"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    "url_with_creds": re.compile(r"://[^/\s:@]{1,64}:[^/\s:@]{1,64}@"),
}
#: ingest 明确拒收的原始会话字段（方案：只收元数据+diff，不收原始会话）
_RAW_SESSION_KEYS = {"messages", "turns", "transcript", "raw", "session_raw",
                     "reasoning", "thinking", "reasoning_chunks"}


def _ensure_dir() -> None:
    try:
        os.makedirs(_BASE, exist_ok=True)
    except Exception:
        pass


def _audit(event: str, subject: str, payload: Dict[str, Any]) -> None:
    """审计链留痕（best-effort，失败不影响主流程）。"""
    try:
        from agent.audit.facade import audit
        audit.record(event, actor="plugins.experience", subject=subject,
                     payload=payload, source="agent")
    except Exception:
        pass


def _read_corpus() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.isfile(_CORPUS):
        return out
    with open(_CORPUS, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def _append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    _ensure_dir()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _review_state() -> Dict[str, Dict[str, Any]]:
    """最新一条 review 事件即当前状态。"""
    state: Dict[str, Dict[str, Any]] = {}
    for ev in _read_jsonl(_REVIEWS):
        if ev.get("id"):
            state[ev["id"]] = ev
    return state


def _scan_hard_block(obj: Any) -> Optional[str]:
    """递归扫描硬阻断模式。只返回模式名，不返回命中值。"""
    if isinstance(obj, dict):
        for v in obj.values():
            hit = _scan_hard_block(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _scan_hard_block(v)
            if hit:
                return hit
    elif isinstance(obj, str):
        for name, rx in _HARD_BLOCK.items():
            if rx.search(obj):
                return name
    return None


def _atomic_write_corpus(rows: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    tmp = _CORPUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, _CORPUS)


# ════════════════════════════════════════════════════════════
#  1) ingest —— 只收元数据 + diff，不收原始会话
# ════════════════════════════════════════════════════════════

@bp.route("/api/experience/ingest", methods=["POST"])
@require_token
def experience_ingest():
    body = request.get_json(silent=True) or {}
    items = body.get("samples")
    if not isinstance(items, list) or not items:
        # 兼容单条提交
        items = [body] if body.get("id") else []
    if not items:
        return jsonify({"ok": False, "error": "缺少 samples"}), 400

    # 拒收原始会话字段
    for it in items:
        if isinstance(it, dict) and (_RAW_SESSION_KEYS & set(it.keys())):
            return jsonify({"ok": False, "error": "拒绝：包含原始会话字段（只收元数据+diff）"}), 400

    accepted: List[str] = []
    rejected: List[Dict[str, str]] = []
    with _LOCK:
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                rejected.append({"reason": "missing_id"}); continue
            hit = _scan_hard_block(it)
            if hit:
                rejected.append({"id": it["id"], "reason": "desensitize_hard_block", "pattern": hit})
                _audit("experience.ingest.rejected", str(it["id"]), {"pattern": hit})
                continue
            accepted.append(it["id"])

        if accepted:
            existing = {r.get("id") for r in _read_corpus()}
            new_rows = [it for it in items if isinstance(it, dict)
                        and it.get("id") in set(accepted) and it["id"] not in existing]
            if new_rows:
                _append_rows(new_rows)
            batch_id = uuid.uuid4().hex[:16]
            _append_jsonl(_BATCHES, {"batch_id": batch_id, "ids": accepted,
                                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                     "count": len(accepted), "status": "active"})
            _audit("experience.ingest", batch_id,
                   {"accepted": len(accepted), "rejected": len(rejected)})
        else:
            batch_id = None

    return jsonify({"ok": True, "batch_id": batch_id,
                    "accepted": accepted, "rejected": rejected})


def _append_rows(rows: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    with open(_CORPUS, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ════════════════════════════════════════════════════════════
#  2) list / 3) detail / 6) stats / 7) search  —— 只读
# ════════════════════════════════════════════════════════════

@bp.route("/api/experience/list", methods=["GET"])
def experience_list():
    lang = (request.args.get("lang") or "").strip()
    status = (request.args.get("status") or "").strip()
    ttype = (request.args.get("task_type") or "").strip()
    limit = min(int(request.args.get("limit", 50) or 50), 500)
    offset = max(int(request.args.get("offset", 0) or 0), 0)

    state = _review_state()
    rows = []
    for r in _read_corpus():
        st = (state.get(r.get("id")) or {}).get("action", "pending")
        if status and st != status:
            continue
        if lang and (r.get("stack") or {}).get("lang") != lang:
            continue
        if ttype and r.get("task_type") != ttype:
            continue
        rows.append({
            "id": r.get("id"),
            "task": (r.get("task") or "")[:200],
            "task_type": r.get("task_type"),
            "stack": r.get("stack"),
            "verified": r.get("verified"),
            "n_diffs": len(r.get("diffs") or []),
            "n_pitfalls": len(r.get("pitfalls") or []),
            "created_at": r.get("created_at"),
            "review_status": st,
        })
    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"ok": True, "total": len(rows), "offset": offset,
                    "items": rows[offset:offset + limit]})


@bp.route("/api/experience/<sid>", methods=["GET"])
def experience_detail(sid: str):
    state = _review_state()
    for r in _read_corpus():
        if r.get("id") == sid:
            out = dict(r)
            out["review_status"] = (state.get(sid) or {}).get("action", "pending")
            return jsonify({"ok": True, "item": out})
    return jsonify({"ok": False, "error": "未找到"}), 404


@bp.route("/api/experience/stats", methods=["GET"])
def experience_stats():
    rows = _read_corpus()
    state = _review_state()
    by_verified: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_lang: Dict[str, int] = {}
    pitfalls = 0
    for r in rows:
        by_verified[r.get("verified") or "unknown"] = by_verified.get(r.get("verified") or "unknown", 0) + 1
        by_type[r.get("task_type") or "unknown"] = by_type.get(r.get("task_type") or "unknown", 0) + 1
        lg = (r.get("stack") or {}).get("lang") or "unknown"
        by_lang[lg] = by_lang.get(lg, 0) + 1
        pitfalls += len(r.get("pitfalls") or [])
    by_action: Dict[str, int] = {}
    for ev in state.values():
        by_action[ev.get("action", "?")] = by_action.get(ev.get("action", "?"), 0) + 1
    return jsonify({"ok": True, "stats": {
        "corpus_size": len(rows),
        "by_verified": by_verified, "by_task_type": by_type, "by_lang": by_lang,
        "pitfalls_total": pitfalls,
        "review": by_action,
        "batches": len(_read_jsonl(_BATCHES)),
    }})


@bp.route("/api/experience/search", methods=["GET"])
def experience_search():
    """仅供 UI 预检预览 —— 纯本地检索，不走 DeepSeek。"""
    q = (request.args.get("q") or "").strip()
    top_k = min(int(request.args.get("top_k", 5) or 5), 20)
    lang = (request.args.get("lang") or "").strip() or None
    if not q:
        return jsonify({"ok": False, "error": "缺少 q"}), 400
    try:
        from agent.skills_mgmt.experience_index import ExperienceIndex
        idx = ExperienceIndex(_CORPUS, use_vector=False)   # UI 预览用 BM25，避免加载模型
        idx.load(); idx.build()
        hits = idx.search(q, top_k=top_k, lang=lang, min_bm25_score=_MIN_SCORE)
        return jsonify({"ok": True, "hits": [
            {"id": h["id"], "score": h["score"], "legs": sorted(h["legs"].keys()),
             "task": (h["meta"].get("description") or "")[:200],
             "task_type": h["meta"].get("_task_type"),
             "lang": h["meta"].get("_lang"),
             "verified": h["meta"].get("_verified")}
            for h in hits]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": "检索不可用: %s" % str(exc)[:200]}), 500


# ════════════════════════════════════════════════════════════
#  4) review（L2 逐次确认）
# ════════════════════════════════════════════════════════════

@bp.route("/api/experience/<sid>/review", methods=["POST"])
@require_token
def experience_review(sid: str):
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip()
    if action not in ("accept", "reject", "deprecate"):
        return jsonify({"ok": False, "error": "action 必须是 accept|reject|deprecate"}), 400
    # L2 语义：逐次确认，单次有效（不设持久开关，避免"一次确认永久放行"）
    if body.get("confirmed") is not True:
        return jsonify({"ok": False, "error": "需要 L2 确认：请求体须含 confirmed=true"}), 400
    if not any(r.get("id") == sid for r in _read_corpus()):
        return jsonify({"ok": False, "error": "未找到"}), 404
    ev = {"id": sid, "action": action, "reason": str(body.get("reason") or "")[:300],
          "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with _LOCK:
        _append_jsonl(_REVIEWS, ev)
    _audit("experience.review", sid, {"action": action})
    return jsonify({"ok": True, "review": ev})


# ════════════════════════════════════════════════════════════
#  5) batch rollback（L2 + 审计链留痕；本域无 L3，见模块 docstring）
# ════════════════════════════════════════════════════════════

@bp.route("/api/experience/batch/<batch_id>/rollback", methods=["POST"])
@require_token
def experience_rollback(batch_id: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return jsonify({"ok": False, "error": "需要确认：请求体须含 confirmed=true"}), 400
    batches = _read_jsonl(_BATCHES)
    target = None
    for b in batches:
        if b.get("batch_id") == batch_id:
            target = b
    if target is None:
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    if target.get("status") == "rolled_back":
        return jsonify({"ok": False, "error": "该批次已回滚"}), 409

    ids = set(target.get("ids") or [])
    with _LOCK:
        rows = _read_corpus()
        kept = [r for r in rows if r.get("id") not in ids]
        removed = len(rows) - len(kept)
        _atomic_write_corpus(kept)
        _append_jsonl(_BATCHES, {"batch_id": batch_id, "ids": sorted(ids),
                                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "count": len(ids), "status": "rolled_back",
                                 "removed": removed})
    # 回滚凭据 = batch_id，写入审计链
    _audit("experience.rollback", batch_id,
           {"requested": len(ids), "removed": removed,
            "reason": str(body.get("reason") or "")[:300]})
    return jsonify({"ok": True, "batch_id": batch_id, "removed": removed,
                    "remaining": len(kept)})


PLUGIN = register_plugin(Plugin(
    name="experience",
    version="1.0.0",
    description="DSH 历史会话提炼的经验库：审阅 / 检索 / 批量回滚（方案 P3）",
    # 【必需】blueprint 缺失时 loader.register_blueprints 会直接 continue
    # （plugins/loader.py:143-144），路由永不挂载 —— 集成检查实测踩过此坑。
    blueprint=bp,
    routes=[
        "/api/experience/ingest",
        "/api/experience/list",
        "/api/experience/<id>",
        "/api/experience/<id>/review",
        "/api/experience/batch/<id>/rollback",
        "/api/experience/stats",
        "/api/experience/search",
    ],
))
