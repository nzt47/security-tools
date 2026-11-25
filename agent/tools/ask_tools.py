"""E1-3a 交互工具：`ask_user_question`（结构化提问）与 `present`（交付声明）。

设计页 `docs/plan/2026-11-25-e1-3-interaction-design.md`：
  · ask 不阻塞：返回 ``USER_INPUT_REQUIRED``，由 chat 循环提升为 SSE ``ask`` 事件，
    本轮结束；用户回答后按普通用户消息继续（与审批边界同一模式）。
  · present 只做**字符串校验 + 声明**，不读取文件 ⇒ 无文件系统 sink（无 path-injection 面）。
"""
from __future__ import annotations

import logging
import re

import agent.tools as _tools
from agent.context import ask as _ask
from agent.context import session_bind

logger = logging.getLogger(__name__)

MAX_PRESENT_FILES = 20
MAX_PATH_CHARS = 500

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def resolve_session() -> str:
    try:
        return session_bind.current()
    except Exception:  # noqa: BLE001 绑定层故障不得击穿工具链
        return ""


def _safe_rel_path(raw) -> str:
    """校验并归一化一个**相对路径**（纯字符串；不做任何文件 IO）

    拒绝：空、绝对路径（/ 或盘符）、`..`/`.`/空段、控制字符、超长。
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("路径为空")
    if len(text) > MAX_PATH_CHARS:
        raise ValueError("路径过长（>%d 字符）" % MAX_PATH_CHARS)
    if text.startswith("/") or _DRIVE_RE.match(text):
        raise ValueError("只接受相对路径：%s" % text[:80])
    if any(ord(ch) < 32 for ch in text):
        raise ValueError("路径含控制字符")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("路径不得含空段/./..：%s" % text[:80])
    return text


def normalize_files(files) -> list:
    """把 present 的 files 归一化为 [{path, title?}]（数量/长度/路径校验）"""
    if files is None:
        raise ValueError("files 不能为空")
    if not isinstance(files, (list, tuple)):
        raise ValueError("files 必须是数组")
    if len(files) > MAX_PRESENT_FILES:
        raise ValueError("files 最多 %d 个" % MAX_PRESENT_FILES)
    out = []
    for i, raw in enumerate(files):
        if isinstance(raw, str):
            path, title = raw, ""
        elif isinstance(raw, dict):
            path = raw.get("path")
            title = str(raw.get("title") or "").strip()[:200]
        else:
            raise ValueError("第 %d 个文件格式不合法" % (i + 1))
        item = {"path": _safe_rel_path(path)}
        if title:
            item["title"] = title
        out.append(item)
    return out


def run_ask(session_id: str, question: str, options=None,
            allow_multiple: bool = False, allow_custom: bool = True,
            context: str = "") -> dict:
    """创建待答问题并返回给模型的结果（非错误，带 error_code 供 SSE 提升）"""
    if not session_id:
        return {"ok": False,
                "error": "无法确定会话上下文（提问必须在一次会话对话里发起）"}
    try:
        doc = _ask.create_question(session_id, question, options=options,
                                   allow_multiple=allow_multiple,
                                   allow_custom=allow_custom, context=context)
    except _ask.AskError as exc:
        return {"ok": False, "error": exc.message, "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        logger.error("[ask] 创建提问异常: %s", exc, exc_info=True)
        return {"ok": False, "error": "创建提问异常: %s" % exc}
    return {
        "ok": False,
        "error_code": "USER_INPUT_REQUIRED",
        "question_id": doc["question_id"],
        "question": doc["question"],
        "options": doc["options"],
        "allow_multiple": doc["allow_multiple"],
        "allow_custom": doc["allow_custom"],
        "context": doc["context"],
        "guidance": ("已向用户展示提问；本轮请结束并等待回答。"
                     "无交互界面时请按默认继续，并在最终回复中说明假设。"),
    }


def run_present(session_id: str, files, note: str = "") -> dict:
    """校验并声明交付文件（只回显路径，不读取文件）"""
    if not session_id:
        return {"ok": False,
                "error": "无法确定会话上下文（交付声明必须在一次会话对话里发起）"}
    try:
        presented = normalize_files(files)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "code": "invalid_files"}
    except Exception as exc:  # noqa: BLE001
        logger.error("[present] 校验异常: %s", exc, exc_info=True)
        return {"ok": False, "error": "交付声明校验异常: %s" % exc}
    return {"ok": True, "presented": presented, "count": len(presented),
            "note": str(note or "").strip()[:1000]}


def register_all(dl):
    """注册交互工具（ask_user_question / present）"""

    @_tools.register(
        "ask_user_question",
        "向用户提出**结构化问题**（单选/多选/自定义），等用户回答后再继续。"
        "适用：需求有歧义、有多个可行方案需要用户拍板、缺少关键信息（路径/账号/口径）。"
        "options 最多 8 个（每项 {id?, label, description?}）；allow_multiple 允许多选；"
        "allow_custom 允许用户自己填。提交后本轮应结束等待；无交互界面时按默认继续并说明假设。"
        "Ask the user a structured question (single/multi choice or custom), then wait.",
        schema={
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "要问的问题（≤1000 字，一句话说清）"},
                "options": {
                    "type": "array",
                    "description": "可选项（最多 8 个）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "选项 id（可省略）"},
                            "label": {"type": "string", "description": "选项文字（≤100 字）"},
                            "description": {"type": "string", "description": "选项说明（可选）"},
                        },
                        "required": ["label"],
                    },
                },
                "allow_multiple": {"type": "boolean", "description": "是否允许多选（默认 false）"},
                "allow_custom": {"type": "boolean",
                                 "description": "是否允许自定义回答（默认 true）"},
                "context": {"type": "string", "description": "补充上下文（≤500 字）"},
            },
            "required": ["question"],
        },
    )
    def _ask_tool(**kwargs):
        return run_ask(
            resolve_session(), str(kwargs.get("question") or ""),
            options=kwargs.get("options"),
            allow_multiple=bool(kwargs.get("allow_multiple", False)),
            allow_custom=bool(kwargs.get("allow_custom", True)),
            context=str(kwargs.get("context") or ""),
        )

    @_tools.register(
        "present",
        "声明本次交付的文件（在回复下方以卡片展示，用户可复制路径/在工作区打开）。"
        "files 为相对路径数组（可带 title）；**只声明、不读取文件内容**；最多 20 个。"
        "用于交付报告/补丁/文档/截图等产物，让用户一眼看到改了哪些文件。"
        "Present deliverable files as cards under the reply.",
        schema={
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": "交付文件（相对路径，最多 20 个）",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "title": {"type": "string"},
                                },
                                "required": ["path"],
                            },
                        ]
                    },
                },
                "note": {"type": "string", "description": "交付说明（可选，≤1000 字）"},
            },
            "required": ["files"],
        },
    )
    def _present_tool(**kwargs):
        return run_present(resolve_session(), kwargs.get("files"),
                           str(kwargs.get("note") or ""))


__all__ = ["MAX_PRESENT_FILES", "normalize_files", "register_all",
           "resolve_session", "run_ask", "run_present"]
