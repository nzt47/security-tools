"""xlsx_read 工具（E3-2 纯库路线）：把 Excel 工作簿的取值读成矩阵。

【为什么与 docx_read 分开】两者都是"读文档"，但返回形状不同（docx 是段落+表格，xlsx 是
表→行→列矩阵）。塞进一个工具会让 schema 变成 `if 扩展名 == ...` 的分支；分开后模型看描述就能选对。
"""
from __future__ import annotations

from typing import Any, Dict

from agent import tools as _tools


def register_all(*_args, **_kwargs) -> int:
    """注册 xlsx_read（与其它 *_tools.register_all 同形；返回注册个数）"""

    @_tools.register(
        "xlsx_read",
        "读取 .xlsx（Excel）工作簿的**单元格取值**（每张表按行×列返回；不含公式原文与格式）。不依赖 Office / openpyxl；输出有上限，超限会标 truncated。Read values from a .xlsx file",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "xlsx 文件路径（必填）"},
            },
            "required": ["path"],
        })
    def _xlsx_read(**kwargs) -> Dict[str, Any]:
        from agent.office.xlsx_text import extract
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
