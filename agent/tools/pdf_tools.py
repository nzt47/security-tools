"""工具注册模块 — PDF 工具（读取、合并、拆分、信息查询）"""
import logging
import json
import uuid
from agent import tools as _tools

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



def register_all(dl):
    """注册所有 PDF 工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    from agent.pdf_tools import (
        read_pdf_text, merge_pdfs, split_pdf, get_pdf_info,
    )

    @_tools.register("read_pdf", "读取 PDF 文件中的文本内容（支持指定页码范围）", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "PDF 文件路径"},
            "pages": {
                "type": "array", "items": {"type": "integer"},
                "description": "要读取的页码列表（1-based），如 [1, 3, 5]，不传则读取全部页面",
            },
        },
        "required": ["path"],
    })
    def _read_pdf(**kwargs):
        path = kwargs.get("path", "")
        pages = kwargs.get("pages")
        if not path:
            return {"ok": False, "error": "请提供 PDF 文件路径（path）"}
        return read_pdf_text(path, pages=pages)

    @_tools.register("merge_pdf", "合并多个 PDF 文件为一个。paths 是要合并的源文件列表，output_path 是输出路径", schema={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array", "items": {"type": "string"},
                "description": "要合并的 PDF 文件路径列表",
            },
            "output_path": {"type": "string", "description": "合并后的输出文件路径"},
        },
        "required": ["paths", "output_path"],
    })
    def _merge_pdf(**kwargs):
        paths = kwargs.get("paths", [])
        output_path = kwargs.get("output_path", "")
        if not paths:
            return {"ok": False, "error": "请提供要合并的文件列表（paths）"}
        if not output_path:
            return {"ok": False, "error": "请提供输出文件路径（output_path）"}
        # PermissionSystem 安全检查
        perm = dl._permission.check_action(f"write_file:{output_path}", f"合并 PDF 到 {output_path}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
        return merge_pdfs(paths, output_path)

    @_tools.register("split_pdf", "拆分 PDF 文件为多个独立的 PDF。支持按指定页范围拆分或每页拆为一个文件", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "源 PDF 文件路径"},
            "output_dir": {"type": "string", "description": "输出目录"},
            "ranges": {
                "type": "array", "items": {
                    "type": "array", "items": {"type": "integer"},
                    "minItems": 2, "maxItems": 2,
                },
                "description": "页范围列表（1-based），如 [[1,3], [5,7]] 表示拆分为第1-3页和第5-7页两个文件，不传则每页拆为一个文件",
            },
        },
        "required": ["path", "output_dir"],
    })
    def _split_pdf(**kwargs):
        path = kwargs.get("path", "")
        output_dir = kwargs.get("output_dir", "")
        ranges = kwargs.get("ranges")
        if not path:
            return {"ok": False, "error": "请提供源 PDF 文件路径（path）"}
        if not output_dir:
            return {"ok": False, "error": "请提供输出目录（output_dir）"}
        # PermissionSystem 安全检查
        perm = dl._permission.check_action(f"write_dir:{output_dir}", f"拆分 PDF 到目录 {output_dir}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
        return split_pdf(path, output_dir, ranges=ranges)

    @_tools.register("get_pdf_info", "获取 PDF 文件的元信息（页数、标题、作者、创建时间、文件大小等）", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "PDF 文件路径"},
        },
        "required": ["path"],
    })
    def _get_pdf_info(**kwargs):
        path = kwargs.get("path", "")
        if not path:
            return {"ok": False, "error": "请提供 PDF 文件路径（path）"}
        return get_pdf_info(path)


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "pdf_tools",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
