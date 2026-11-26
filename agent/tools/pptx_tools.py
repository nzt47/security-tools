"""pptx_read 工具（E3-2 纯库路线）：把演示文稿每页的正文与表格读成文本。

【为什么是一个独立工具而不是塞进 read_file】read_file 的契约是"按字节读文件"（二进制回
`binary=True`），给它加格式分支会让所有调用方都要理解"有时不是原文"；pptx 抽取是另一种操作，
单独一个工具语义更清楚，也不会让 read_file 的返回形状分叉（与 docx_read / xlsx_read 同套路）。
"""
from __future__ import annotations

from typing import Any, Dict

from agent import tools as _tools


def register_all(*_args, **_kwargs) -> int:
    """注册 pptx_read（与其它 *_tools.register_all 同形；返回注册个数）"""

    @_tools.register(
        "pptx_read",
        "读取 .pptx（PowerPoint）演示文稿的**每页正文段落与表格文本**（纯文本，不含版式/图片/备注/母版）。"
        "不依赖 Office 或 LibreOffice；输出有上限，超限会标 truncated。Read text from a .pptx file",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "pptx 文件路径（必填）"},
            },
            "required": ["path"],
        })
    def _pptx_read(**kwargs) -> Dict[str, Any]:
        from agent.office.pptx_text import extract
        from agent.system_tools import safe_resolve_path

        raw = str(kwargs.get("path") or "").strip()
        if not raw:
            return {"ok": False, "error": "缺少 path"}
        try:
            path = safe_resolve_path(raw)
        except ValueError:
            return {"ok": False, "error": "路径不允许访问"}
        return extract(path)

    return 1


__all__ = ["register_all"]
