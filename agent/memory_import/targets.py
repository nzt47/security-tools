"""载体写入器：知识卡片 / 长期记忆（可注入，便于测试与灰度）。

【单一事实源】本模块不新建任何存储，只调用云枢既有写路径：
- 知识卡片 -> agent.knowledge.card.CardStore.create（与 /api/knowledge/cards 同源）；
- 长期记忆 -> 运行中服务的 POST /api/memory/manual（与前端"手动添加记忆"同源；
  不 import app_server，避免在 CLI 进程里重建 DigitalLife）。

【为什么可注入】commit 是唯一会"真写"的一步，测试必须能在不碰真实库的前提下
验证"没确认就不写、确认了写什么、写失败如何降级"。因此 service 允许把
writers 换成一堆假函数（鸭子类型：接收 Batch，返回 dict 或抛异常）。

【不易】卡片 slug 冲突不覆盖：CardStore.create 对同 slug 抛 CardConflictError，
这里按 -2/-3 后缀消歧并置 explicit_slug=True（与 distill 的消歧口径一致）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .models import CARRIER_CARD, CARRIER_MEMORY, Batch


@dataclass
class WriteOutcome:
    ok: bool
    carrier: str
    ref: str = ""
    error: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "carrier": self.carrier, "ref": self.ref,
                "error": self.error, "detail": dict(self.detail)}


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def _card_insight(batch: Batch, limit: int = 120) -> str:
    for line in str(batch.digest or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith(">"):
            continue
        text = text.lstrip("- ").strip()
        if text:
            return text[:limit]
    return f"{batch.source} 的{batch.date or '历史'}偏好与规则（外部记忆导入）"


def write_knowledge_card(batch: Batch, *, store: Any = None,
                         status: str = "current") -> Dict[str, Any]:
    """把批次写成一张知识卡片（默认 current；可显式传 draft）。"""
    if batch.carrier != CARRIER_CARD:
        raise ValueError(f"载体不匹配: {batch.carrier!r} != {CARRIER_CARD!r}")
    from agent.knowledge.card import CardConflictError
    from agent.knowledge.schema import Card, slugify

    if store is None:
        from agent.knowledge.card import CardStore
        store = CardStore()
    base_slug = slugify(batch.title) or slugify(batch.batch_id)
    insight = _card_insight(batch)
    body = str(batch.digest or "").strip()
    last_error: Optional[Exception] = None
    for attempt in range(1, 6):
        slug = base_slug if attempt == 1 else f"{base_slug}-{attempt}"
        card = Card(
            title=batch.title, slug=slug, status=str(status or "current"),
            type="concepts",
            source=f"import:{batch.source}:{batch.date or 'static'}",
            date=_today(), tags=[t for t in batch.tags if t],
            insight=insight, content=body,
            metadata={"import_batch": batch.batch_id, "records": len(batch.record_ids),
                      "source": batch.source},
            explicit_slug=(attempt > 1),
        )
        try:
            created = store.create(card)
            ref = str(getattr(created, "slug", "") or slug)
            return {"ok": True, "carrier": CARRIER_CARD, "ref": ref,
                    "detail": {"slug": ref, "status": str(status or "current"),
                               "chars": len(body), "type": "concepts"}}
        except CardConflictError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"卡片 slug 连续冲突，放弃写入：{last_error}")


#: 写入长期记忆的 HTTP 超时默认值（秒）；可被 CP_MEMORY_IMPORT_HTTP_TIMEOUT 覆盖
#: （规则 memory_import.http_timeout_sec，见 agent/monitoring/observability_config.py）
HTTP_TIMEOUT_SEC = 30.0
HTTP_TIMEOUT_ENV = "CP_MEMORY_IMPORT_HTTP_TIMEOUT"


def http_timeout(default: float = HTTP_TIMEOUT_SEC) -> float:
    """读 HTTP 超时：非法/越界回退默认值（合法区间 1-300 秒）。"""
    import os

    raw = str(os.environ.get(HTTP_TIMEOUT_ENV, "") or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    return value if 1.0 <= value <= 300.0 else float(default)


def read_api_token(*, root: Optional[Any] = None) -> str:
    """取云枢 API 令牌：先环境变量，再仓库 .env（与 deploy 脚本同源）。

    【为什么读 .env】CLI 在独立进程里跑，拿不到服务进程的环境；而
    /api/memory/manual 在 enforce 档下必须带令牌。令牌只用于请求头，
    不写日志、不进审计载荷、不进批次清单。
    """
    import os
    from pathlib import Path

    # 只读一个已登记开关；写成字面量而不是循环变量，AST 扫描器才认得出读取点
    value = str(os.environ.get("FLASK_API_TOKEN") or "").strip()
    if value:
        return value
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    env_file = base / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("FLASK_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            return ""
    return ""


def _default_memory_sink(content: str, *, priority: str = "low",
                        base_url: str = "http://127.0.0.1:5678",
                        timeout: Optional[float] = None) -> Dict[str, Any]:
    """默认写入长期记忆：走**运行中服务**的 /api/memory/manual。

    【为什么走 HTTP 而不是直接 import app_server】app_server 在模块级构造
    DigitalLife（实测 80–100s，会加载 embedding/reranker）；CLI 进程里再建
    一个实例既慢又会与正在服务的进程产生第二份记忆状态。走既有 HTTP 写路径
    与前端"手动添加记忆"完全同源，服务端的记忆层负责加密落盘。
    """
    import json
    import urllib.error
    import urllib.request

    token = read_api_token()
    timeout = http_timeout() if timeout is None else float(timeout)
    body = json.dumps({"content": content, "priority": priority},
                      ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(base_url.rstrip("/") + "/api/memory/manual",
                                     data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"记忆写入被拒绝（HTTP {exc.code}）：{detail}") from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, 200):
        raise RuntimeError(f"记忆写入失败：{payload}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "记忆写入失败"))
    # 【2026-10-25 · 交接单 T3 遗留】把服务端返回的 msg_id 落进 detail：
    # 没有它，这条长期记忆**事后就没有任何稳定入口可删**（按序号删已明确 501）。
    msg_id = str((data or {}).get("msg_id") or "") if isinstance(data, dict) else ""
    return {"ok": True, "ref": "memory/manual",
            "detail": {"msg_id": msg_id} if msg_id else {}}


def delete_memory_note(msg_id: str, *,
                       base_url: str = "http://127.0.0.1:5678",
                       timeout: Optional[float] = None,
                       dry_run: bool = False) -> Dict[str, Any]:
    """按 id 删除一条长期记忆：走**运行中服务**的 DELETE /api/memory/messages/<id>。

    与写入同源（HTTP），理由见 `_default_memory_sink`：CLI 进程里再造一个
    DigitalLife 既慢又会与线上进程产生第二份状态。

    【判据】真删必须在请求体里显式 `dry_run=False & confirm=True`；
    这里不接受"只给 msg_id 就删"，避免调用方忘记确认语义。

    【404 = 已经不在了，不是失败】msg_id 就是落盘 timestamp，而**时间戳可能撞**：
    实测 2026-10-24 那批里 mb_c1624e74c7dd 与 mb_02fde7f96c1b 共用同一条 timestamp，
    按 id 删除会一次删掉两行 —— 后一个调用必然 404。把它当异常抛出来，调用方
    （迁移器）就会"补偿删除刚写的向量"，结果那一批的正文**两边都没了**（真实事故，
    2026-10-25 首次迁移时发生并用计划快照 digest 恢复）。故这里与 delete_vector_item
    同口径：404 返回 `missing=True`（目标状态已达成），只有真失败才抛。
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    target = str(msg_id or "").strip()
    if not target:
        raise ValueError("msg_id 不能为空")
    token = read_api_token()
    timeout = http_timeout() if timeout is None else float(timeout)
    body = json.dumps({"dry_run": bool(dry_run), "confirm": not dry_run},
                      ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Token"] = token
    url = (base_url.rstrip("/") + "/api/memory/messages/"
           + urllib.parse.quote(target, safe=""))
    request = urllib.request.Request(url, data=body, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # 已经被删掉了（含"同 timestamp 的另一条把它一起删了"）⇒ 目标状态已达成
            return {"ok": True, "deleted": False, "missing": True, "msg_id": target,
                    "dry_run": False, "audit": {}}
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"删除长期记忆被拒绝（HTTP {exc.code}）：{detail}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"删除长期记忆的响应不是统一信封：{str(payload)[:120]}")
    return {"ok": bool(data.get("ok")) and (bool(data.get("deleted")) or bool(dry_run)),
            "deleted": bool(data.get("deleted")), "msg_id": target,
            "dry_run": bool(data.get("dry_run")), "audit": data.get("audit") or {},
            # dry_run=true 时服务端会回 found/preview ⇒ 可当"这条还在不在"的探针用
            # （迁移器在补偿删向量前必须先确认原消息是否还在，见 migrate.migrate）
            "found": bool(data.get("found", True)),
            "preview": str(data.get("preview") or "")}


#: 向量记忆 metadata 的来源标记（导入的向量一律带它，检索/回滚/审计都靠它辨认）
VECTOR_SOURCE = "memory_import"


def vector_metadata(batch: Batch) -> Dict[str, Any]:
    """批次 -> 向量 memory metadata（**可过滤、可回读、可追责**的最小子集）。

    【为什么是这五个键】来源（source）、哪一批（batch_id）、哪个外部来源（origin）、
    哪段时间（period）、摘要方式（digest_source）—— 少一个就无法回答
    "这条向量是谁写的、能不能按批回滚、是不是 LLM 改写的"。
    title/records 只是方便人读，不参与判据。
    """
    return {
        "source": VECTOR_SOURCE,
        "batch_id": str(batch.batch_id or ""),
        "origin": str(batch.source or ""),
        "period": str(batch.date or ""),
        "digest_source": str(getattr(batch, "digest_source", "rule") or "rule"),
        "title": str(batch.title or ""),
        "records": len(batch.record_ids),
    }


def _default_vector_sink(content: str, *, metadata: Optional[Dict[str, Any]] = None,
                         base_url: str = "http://127.0.0.1:5678",
                         timeout: Optional[float] = None) -> Dict[str, Any]:
    """默认写入向量记忆：走**运行中服务**的 POST /api/vector/add。

    【为什么改走向量记忆（交接单 §10）】此前写的是长期记忆消息流：落盘 role=user，
    会被 get_context / 压缩器当成本机对话折进滚动摘要 —— 外部转录悄悄混进本机记忆，
    而且事后查不出来（实测 /api/vector/search 搜不到，等于既不真实也检索不到）。
    向量记忆只被语义检索读到，可按 metadata 过滤、可按 id 删除。

    【为什么走 HTTP 而不是 import app_server】与 `_default_memory_sink` 同源：
    app_server 模块级构造 DigitalLife（实测 80-100s），CLI 进程里再造一份会与
    正在服务的进程产生第二份状态。HTTP 写路径与界面"手动添加"完全同源。
    """
    import json
    import urllib.error
    import urllib.request

    token = read_api_token()
    timeout = http_timeout() if timeout is None else float(timeout)
    payload = {"content": content, "metadata": dict(metadata or {})}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(base_url.rstrip("/") + "/api/vector/add",
                                     data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"向量写入被拒绝（HTTP {exc.code}）：{detail}") from exc
    # 该端点是 legacy 形状（{ok, item_id}），不是统一信封；两种都认，免得换壳就废
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict) or data.get("ok") is False:
        raise RuntimeError(f"向量写入失败：{str(raw)[:160]}")
    item_id = str(data.get("item_id") or "").strip()
    if not item_id:
        raise RuntimeError("向量写入未返回 item_id（无法事后按 id 回滚）")
    return {"ok": True, "ref": "vector/" + item_id, "detail": {"vector_ids": [item_id]}}


def delete_vector_item(item_id: str, *,
                       base_url: str = "http://127.0.0.1:5678",
                       timeout: Optional[float] = None) -> Dict[str, Any]:
    """按 id 删除一条向量记忆：走 DELETE /api/vector/item/<id>（#1299）。

    【判据】"本来就没有"与"删掉了"分开报：HTTP 404 说明它已经不在库里，
    对**回滚**而言目标状态已达成，故 missing=True + ok=True；真正失败
    （5xx/网络）才抛异常，由调用方如实回 manual 且**不删清单**。
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    target = str(item_id or "").strip()
    if not target:
        raise ValueError("item_id 不能为空")
    token = read_api_token()
    timeout = http_timeout() if timeout is None else float(timeout)
    headers = {}
    if token:
        headers["X-API-Token"] = token
    url = (base_url.rstrip("/") + "/api/vector/item/"
           + urllib.parse.quote(target, safe=""))
    request = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"ok": True, "deleted": False, "missing": True, "item_id": target}
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"删除向量记忆被拒绝（HTTP {exc.code}）：{detail}") from exc
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"删除向量记忆的响应不是统一信封：{str(raw)[:120]}")
    return {"ok": bool(data.get("deleted")), "deleted": bool(data.get("deleted")),
            "missing": False, "item_id": target, "audit": data.get("audit") or {}}


def write_memory_note(batch: Batch, *, sink: Optional[Callable[..., Any]] = None,
                      priority: str = "low") -> Dict[str, Any]:
    """把批次写成一条**向量记忆**（规则式摘要，不做 LLM 改写）。

    【单一载体，不双写】正文只进向量库；metadata 带
    {source: memory_import, batch_id, origin, period, digest_source}，
    回滚凭写入回执里的 vector_ids 按 id 真删（见 service.rollback）。
    """
    if batch.carrier != CARRIER_MEMORY:
        raise ValueError(f"载体不匹配: {batch.carrier!r} != {CARRIER_MEMORY!r}")
    body = str(batch.digest or "").strip()
    if not body:
        raise ValueError("批次正文为空，拒绝写入")
    body = f"[外部记忆导入 {batch.batch_id} | {batch.title}]\n{body}"
    meta = vector_metadata(batch)
    writer = sink or _default_vector_sink
    result = writer(body, metadata=meta) or {}
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or "向量记忆写入失败"))
    detail = dict((result or {}).get("detail") or {}) if isinstance(result, dict) else {}
    vector_ids = [str(v) for v in (detail.get("vector_ids") or []) if str(v)]
    if not vector_ids:
        # 没有 id 就没有回滚入口 —— 宁可当场失败，也不要写进一条删不掉的向量
        raise RuntimeError("向量写入未留下 vector_ids（回滚将无据可依）")
    ref = str((result or {}).get("ref") or "") if isinstance(result, dict) else ""
    return {"ok": True, "carrier": CARRIER_MEMORY, "ref": ref or vector_ids[0],
            "detail": {"chars": len(body), "priority": priority,
                       "records": len(batch.record_ids),
                       "vector_ids": vector_ids, "metadata": meta}}

def default_writers() -> Dict[str, Any]:
    """默认载体写入器（commit 用；测试应替换成假函数）。"""
    return {
        CARRIER_CARD: write_knowledge_card,
        CARRIER_MEMORY: write_memory_note,
    }


__all__ = [
    "WriteOutcome", "default_writers", "write_knowledge_card", "write_memory_note",
]
