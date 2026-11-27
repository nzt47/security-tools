"""mcp_resource 工具（E4-3）：MCP resources / prompts 的**只读**模型侧入口。

【为什么是**一个**工具 + action 枚举】四个动作是"同一个连接对象的四种读"。
拆成四个工具会让工具索引 / 能力清单 / BM25 半饱和点 / 路由 τ 连着改四次；
schema 用 action 枚举表达，错误能就地说清缺哪个参数（与 terminal 同一处置）。

【边界（设计稿 §1 明确写死）】
  · 只读：不 subscribe、不写资源、不落盘；
  · 不把 prompt 模板自动拼进系统提示词（模板内容由第三方服务器提供，只能作为
    **数据**回给模型，由模型显式使用）；
  · 只作用于**已连接**的服务器（连接经 connect_mcp / ext_install 建立，绕过
    签名/白名单不在本工具的职责内）。

【能力协商必须读回】服务器没声明 `resources`/`prompts` ⇒ 回 `not_supported`，
**不是空列表**（两者对运维的含义完全不同）。判定发生在 agent/tools/mcp_connector.py
（唯一事实源）；本层只做参数校验、大小上限、审计与结果整形。

【失败码（稳定，前端/运维可直接判读）】
  bad_request（未知 action / 缺参 / arguments 非对象）、no_connection（连接不存在或
  传输失败）、not_supported（服务器未声明该能力）、too_large（超过
  CP_MCP_RESOURCE_MAX_BYTES）、not_text（资源是 blob/二进制，不入上下文）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 单个资源/提示词模板的字节上限（默认 256KB）。超限**不落盘**：提示改用分页
#: uri 或让服务器暴露子资源 —— 与图片 offload 同口径（二进制/大块不入上下文）。
ENV_MAX_BYTES = "CP_MCP_RESOURCE_MAX_BYTES"
_DEFAULT_MAX_BYTES = 262144
_MIN_MAX_BYTES = 1024
_MAX_MAX_BYTES = 16 * 1024 * 1024

_ACTIONS = ("list_resources", "read_resource", "list_prompts", "get_prompt")

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "description": "要做的动作（全部只读）",
        },
        "connection": {
            "type": "string",
            "description": "MCP 连接 id（list_mcp_connections 返回的那个）",
        },
        "uri": {
            "type": "string",
            "description": "action=read_resource 必填：资源的 uri（list_resources 原样返回）",
        },
        "name": {
            "type": "string",
            "description": "action=get_prompt 必填：提示词模板名（list_prompts 返回的 name）",
        },
        "arguments": {
            "type": "object",
            "description": "action=get_prompt 的参数对象（键/值由模板的 arguments 声明）",
        },
    },
    "required": ["action", "connection"],
}


def max_bytes() -> int:
    """资源/模板的字节上限（env 归一：非法值回落默认，并夹到 [1KB, 16MB]）"""
    raw = os.environ.get(ENV_MAX_BYTES)
    try:
        value = int(raw) if raw not in (None, "") else _DEFAULT_MAX_BYTES
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_BYTES
    return max(_MIN_MAX_BYTES, min(value, _MAX_MAX_BYTES))


def _digest(value: Any) -> str:
    """摘要（**只记摘要，不记原文**：uri 可能含租户/凭据片段）"""
    text = str(value or "")
    if not text:
        return ""
    return "sha256:" + hashlib.sha256(
        text.encode("utf-8", errors="replace")).hexdigest()[:8]


def _audit(action: str, connection: str, target: str, nbytes: int, code: str) -> None:
    """每次读写一条 `mcp_resource_access`（best-effort，失败不阻断）"""
    try:
        from agent.audit.facade import audit

        audit.record(
            "mcp_resource_access",
            actor="mcp",
            subject=str(action or ""),
            payload={
                "action": str(action or ""),
                "connection_digest": _digest(connection),
                "target_digest": _digest(target),
                "bytes": int(nbytes or 0),
                "code": str(code or ""),
            },
            source="agent",
        )
    except Exception as exc:  # noqa: BLE001 审计是旁路
        logger.debug("[mcp_resource] 审计写入失败（%s）", type(exc).__name__)


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "code": str(code), "error": str(message)}
    for key, value in extra.items():
        if value is not None:
            out[key] = value
    return out


def _shape_list(action: str, raw: dict, key: str) -> Dict[str, Any]:
    items = [i for i in (raw.get(key) or []) if isinstance(i, dict)]
    return {"ok": True, "action": action, key: items, "count": len(items)}


def _shape_resource(raw: dict, uri: str) -> Dict[str, Any]:
    contents = [c for c in (raw.get("contents") or []) if isinstance(c, dict)]
    if not contents:
        return {"ok": True, "action": "read_resource", "uri": uri,
                "contents": [], "count": 0, "bytes": 0}
    # 二进制：任一内容项没有 text（blob / 图片）⇒ not_text（不把 base64 塞进上下文）
    for item in contents:
        if "text" not in item:
            return _fail("not_text", "资源不是文本（blob/二进制不入上下文）", uri=uri)
    texts = [str(item.get("text") or "") for item in contents]
    total = sum(len(t.encode("utf-8", errors="replace")) for t in texts)
    limit = max_bytes()
    if total > limit:
        return _fail(
            "too_large",
            "资源 %d 字节，超过上限 %d 字节；不落盘，请改用分页 uri 或让服务器暴露子资源"
            % (total, limit),
            uri=uri, bytes=total, limit=limit)
    out = [
        {"uri": str(item.get("uri") or uri),
         "mimeType": str(item.get("mimeType") or ""),
         "text": text}
        for item, text in zip(contents, texts)
    ]
    return {"ok": True, "action": "read_resource", "uri": uri,
            "contents": out, "count": len(out), "bytes": total}


def _shape_prompt(raw: dict, name: str) -> Dict[str, Any]:
    prompt = raw.get("prompt") or {}
    if not isinstance(prompt, dict):
        return _fail("bad_request", "服务器返回的提示词结构不可识别", name=name)
    try:
        size = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        size = 0
    limit = max_bytes()
    if size > limit:
        return _fail("too_large",
                     "提示词模板 %d 字节，超过上限 %d 字节" % (size, limit),
                     name=name, bytes=size, limit=limit)
    return {
        "ok": True,
        "action": "get_prompt",
        "name": name,
        "description": str(prompt.get("description") or ""),
        "messages": prompt.get("messages") or [],
        "bytes": size,
    }


def execute(action: Any, *, connection: Any = "", uri: Any = "",
            name: Any = "", arguments: Any = None) -> Dict[str, Any]:
    """执行一次只读 resources/prompts 调用（工具 handler 与用例共用这一入口）"""
    act = str(action or "").strip().lower()
    conn_id = str(connection or "").strip()
    target_uri = str(uri or "").strip()
    target_name = str(name or "").strip()

    if act not in _ACTIONS:
        return _fail("bad_request",
                     "不支持的 action（可选 %s）" % " / ".join(_ACTIONS))
    if not conn_id:
        return _fail("bad_request",
                     "action=%s 需要 connection（list_mcp_connections 返回的 id）" % act)
    if act == "read_resource" and not target_uri:
        return _fail("bad_request", "action=read_resource 需要 uri")
    if act == "get_prompt" and not target_name:
        return _fail("bad_request", "action=get_prompt 需要 name")
    if arguments is not None and not isinstance(arguments, dict):
        return _fail("bad_request", "arguments 必须是对象（键值对）")

    from agent.tools.mcp_connector import resource_action

    raw = resource_action(conn_id, act, uri=target_uri, name=target_name,
                          arguments=arguments if isinstance(arguments, dict) else None)
    if not isinstance(raw, dict):
        result = _fail("no_connection", "MCP 调用没有返回结果")
    elif not raw.get("ok"):
        result = _fail(str(raw.get("code") or "no_connection"),
                       str(raw.get("error") or "MCP 调用失败"))
        caps = raw.get("capabilities")
        if isinstance(caps, list):
            result["capabilities"] = caps
    elif act == "list_resources":
        result = _shape_list(act, raw, "resources")
    elif act == "list_prompts":
        result = _shape_list(act, raw, "prompts")
    elif act == "read_resource":
        result = _shape_resource(raw, target_uri)
    else:
        result = _shape_prompt(raw, target_name)

    _audit(act, conn_id, target_uri or target_name,
           int(result.get("bytes") or 0), str(result.get("code") or "ok"))
    return result


def register_all(*_args, **_kwargs) -> int:
    """注册 mcp_resource（与其它 *_tools.register_all 同形；返回注册个数）"""

    @_tools.register(
        "mcp_resource",
        "读取 MCP 服务器的资源与提示词模板（只读）：action=list_resources 列出资源、"
        "read_resource 按 uri 读文本、list_prompts 列出提示词模板、get_prompt 按 name 取回模板。"
        "全部只读：不订阅变更、不写资源、不自动注入上下文；服务器未声明对应能力时回 "
        "not_supported（不是空列表）。Read MCP resources and prompts (read-only)",
        schema=SCHEMA,
        source=_tools.SOURCE_MCP_ADMIN,
    )
    def _mcp_resource(**kwargs) -> Dict[str, Any]:
        return execute(kwargs.get("action"),
                       connection=kwargs.get("connection"),
                       uri=kwargs.get("uri"),
                       name=kwargs.get("name"),
                       arguments=kwargs.get("arguments"))

    return 1


__all__ = ["register_all", "execute", "max_bytes", "ENV_MAX_BYTES", "SCHEMA"]
