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
    return {"ok": True, "ref": "memory/manual"}


def write_memory_note(batch: Batch, *, sink: Optional[Callable[..., Any]] = None,
                      priority: str = "low") -> Dict[str, Any]:
    """把批次写成一条长期记忆（规则式摘要，不做 LLM 改写）。"""
    if batch.carrier != CARRIER_MEMORY:
        raise ValueError(f"载体不匹配: {batch.carrier!r} != {CARRIER_MEMORY!r}")
    body = str(batch.digest or "").strip()
    if not body:
        raise ValueError("批次正文为空，拒绝写入")
    body = f"[外部记忆导入 {batch.batch_id} | {batch.title}]\n{body}"
    writer = sink or _default_memory_sink
    result = writer(body, priority=priority) or {}
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or "长期记忆写入失败"))
    ref = str((result or {}).get("ref") or "") if isinstance(result, dict) else ""
    return {"ok": True, "carrier": CARRIER_MEMORY, "ref": ref or batch.batch_id,
            "detail": {"chars": len(body), "priority": priority,
                       "records": len(batch.record_ids)}}


def default_writers() -> Dict[str, Any]:
    """默认载体写入器（commit 用；测试应替换成假函数）。"""
    return {
        CARRIER_CARD: write_knowledge_card,
        CARRIER_MEMORY: write_memory_note,
    }


__all__ = [
    "WriteOutcome", "default_writers", "write_knowledge_card", "write_memory_note",
]
