"""
PDF 处理工具集 — 读取、提取表格、合并、拆分、元信息查询

依赖:
  - pypdf       (pip install pypdf)      用于读取文本、合并、拆分、元信息
  - pdfplumber  (pip install pdfplumber)  用于提取表格

如果对应库未安装，函数会返回提示安装的错误信息，不会崩溃。
"""

import os
import logging

logger = logging.getLogger(__name__)


def _check_library(name: str, import_name: str | None = None):
    """检查 Python 库是否可用，返回 (module, error_msg)"""
    try:
        mod = __import__(import_name or name)
        return mod, None
    except ImportError:
        return None, f"缺少依赖库 '{name}'，请运行: pip install {name}"


def _normalize_pages(total: int, pages: list[int] | None) -> list[int]:
    """将 pages 参数（1-based）转换为 0-based 的页索引列表。

    如果 pages 为 None，返回所有页的索引。
    """
    if pages is None:
        return list(range(total))
    # 转 0-based 并过滤超出范围的值
    return [p - 1 for p in pages if 1 <= p <= total]


def read_pdf_text(path: str, pages: list[int] | None = None) -> dict:
    """读取 PDF 文件中的文本内容。

    使用 pypdf 库逐页提取文本。如果 pages 参数指定了某些页面，
    则只返回这些页面的内容；否则返回全部页面。

    Args:
        path: PDF 文件路径
        pages: 要提取的页码列表（1-based），如 [1, 3, 5]，
               或 None 表示全部页面

    Returns:
        {
            "ok": bool,
            "text": str,              # 提取的文本内容
            "pages": int,             # 总页数
            "extracted_pages": int,   # 实际提取的页数
            "error": str | None       # 错误信息
        }
    """
    pypdf, err = _check_library("pypdf", "pypdf")
    if err:
        return {"ok": False, "text": "", "pages": 0, "extracted_pages": 0, "error": err}

    if not os.path.exists(path):
        return {"ok": False, "text": "", "pages": 0, "extracted_pages": 0,
                "error": f"文件不存在: {path}"}

    try:
        reader = pypdf.PdfReader(path)
        total = len(reader.pages)
        indices = _normalize_pages(total, pages)

        text_parts = []
        for i in indices:
            page_text = reader.pages[i].extract_text()
            text_parts.append(f"--- 第 {i + 1} 页 ---\n{page_text}")

        return {
            "ok": True,
            "text": "\n\n".join(text_parts),
            "pages": total,
            "extracted_pages": len(indices),
            "error": None,
        }
    except Exception as e:
        logger.exception("读取 PDF 文本失败")
        return {"ok": False, "text": "", "pages": 0, "extracted_pages": 0,
                "error": f"读取 PDF 文本失败: {e}"}


def read_pdf_tables(path: str, pages: list[int] | None = None) -> dict:
    """提取 PDF 文件中的表格数据。

    使用 pdfplumber 库逐页检测并提取表格。每个表格以二维列表
    （行列表，每行为单元格列表）的形式返回。

    Args:
        path: PDF 文件路径
        pages: 要提取的页码列表（1-based），或 None 表示全部页面

    Returns:
        {
            "ok": bool,
            "tables": [                 # 每个元素对应一页的表格
                {
                    "page": int,
                    "tables": [
                        {
                            "index": int,
                            "rows": int,
                            "cols": int,
                            "data": [[str, ...], ...]
                        },
                        ...
                    ]
                },
                ...
            ],
            "pages": int,
            "extracted_pages": int,
            "error": str | None
        }
    """
    pdfplumber, err = _check_library("pdfplumber")
    if err:
        return {"ok": False, "tables": [], "pages": 0, "extracted_pages": 0, "error": err}

    if not os.path.exists(path):
        return {"ok": False, "tables": [], "pages": 0, "extracted_pages": 0,
                "error": f"文件不存在: {path}"}

    try:
        import pdfplumber as pdfplumber_mod
        result_tables = []
        with pdfplumber_mod.open(path) as pdf:
            total = len(pdf.pages)
            indices = _normalize_pages(total, pages)

            for i in indices:
                page = pdf.pages[i]
                raw_tables = page.extract_tables()
                page_tables = []
                for j, table in enumerate(raw_tables):
                    if not table:
                        continue
                    # 清理：将 None 转为空串
                    cleaned = [
                        [cell if cell is not None else "" for cell in row]
                        for row in table
                    ]
                    page_tables.append({
                        "index": j + 1,
                        "rows": len(cleaned),
                        "cols": max(len(r) for r in cleaned) if cleaned else 0,
                        "data": cleaned,
                    })
                result_tables.append({
                    "page": i + 1,
                    "tables": page_tables,
                })

        return {
            "ok": True,
            "tables": result_tables,
            "pages": total,
            "extracted_pages": len(indices),
            "error": None,
        }
    except Exception as e:
        logger.exception("提取 PDF 表格失败")
        return {"ok": False, "tables": [], "pages": 0, "extracted_pages": 0,
                "error": f"提取 PDF 表格失败: {e}"}


def merge_pdfs(paths: list[str], output_path: str) -> dict:
    """合并多个 PDF 文件为一个。

    使用 pypdf 库将多个 PDF 按 paths 列表的顺序逐页合并，
    输出到 output_path。

    Args:
        paths: 要合并的 PDF 文件路径列表
        output_path: 输出文件路径

    Returns:
        {
            "ok": bool,
            "output_path": str,
            "total_pages": int,
            "merged_files": int,
            "error": str | None
        }
    """
    pypdf, err = _check_library("pypdf", "pypdf")
    if err:
        return {"ok": False, "output_path": output_path,
                "total_pages": 0, "merged_files": 0, "error": err}

    if not paths:
        return {"ok": False, "output_path": output_path,
                "total_pages": 0, "merged_files": 0,
                "error": "paths 列表为空，没有文件可合并"}

    # 检查所有输入文件是否存在
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return {"ok": False, "output_path": output_path,
                "total_pages": 0, "merged_files": 0,
                "error": f"以下文件不存在: {missing}"}

    # 确保输出目录存在
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        writer = pypdf.PdfWriter()
        total_pages = 0

        for pdf_path in paths:
            reader = pypdf.PdfReader(pdf_path)
            for page in reader.pages:
                writer.add_page(page)
                total_pages += 1

        with open(output_path, "wb") as f:
            writer.write(f)

        return {
            "ok": True,
            "output_path": os.path.abspath(output_path),
            "total_pages": total_pages,
            "merged_files": len(paths),
            "error": None,
        }
    except Exception as e:
        logger.exception("合并 PDF 失败")
        return {"ok": False, "output_path": output_path,
                "total_pages": 0, "merged_files": 0,
                "error": f"合并 PDF 失败: {e}"}


def split_pdf(path: str, output_dir: str,
              ranges: list[list[int]] | None = None) -> dict:
    """拆分 PDF 文件为多个独立 PDF。

    使用 pypdf 库将 PDF 按指定页范围拆分为多个文件。
    如果未指定 ranges，则将每一页拆分为一个独立的 PDF 文件。

    Args:
        path: 源 PDF 文件路径
        output_dir: 输出目录
        ranges: 页范围列表，每个元素为 [start, end]（1-based，包含两端），
                如 [[1,3], [5,7]] 表示拆分为第1-3页和第5-7页两个文件。
                None 表示每页拆为一个文件。

    Returns:
        {
            "ok": bool,
            "output_dir": str,
            "files": [str, ...],       # 生成的 PDF 文件路径列表
            "total_outputs": int,
            "error": str | None
        }
    """
    pypdf, err = _check_library("pypdf", "pypdf")
    if err:
        return {"ok": False, "output_dir": output_dir,
                "files": [], "total_outputs": 0, "error": err}

    if not os.path.exists(path):
        return {"ok": False, "output_dir": output_dir,
                "files": [], "total_outputs": 0,
                "error": f"文件不存在: {path}"}

    os.makedirs(output_dir, exist_ok=True)

    try:
        reader = pypdf.PdfReader(path)
        total = len(reader.pages)

        # 生成输出文件列表
        output_files = []

        if ranges is None:
            # 每页拆为一个文件
            for i in range(total):
                writer = pypdf.PdfWriter()
                writer.add_page(reader.pages[i])
                out_path = os.path.join(output_dir, f"page_{i + 1:03d}.pdf")
                with open(out_path, "wb") as f:
                    writer.write(f)
                output_files.append(os.path.abspath(out_path))
        else:
            for idx, (start, end) in enumerate(ranges):
                # 转 0-based，并限制范围
                s = max(0, start - 1)
                e = min(total - 1, end - 1)
                if s > e or s < 0 or e >= total:
                    continue
                writer = pypdf.PdfWriter()
                for i in range(s, e + 1):
                    writer.add_page(reader.pages[i])
                out_path = os.path.join(output_dir, f"split_{idx + 1:03d}_p{start}-{end}.pdf")
                with open(out_path, "wb") as f:
                    writer.write(f)
                output_files.append(os.path.abspath(out_path))

        return {
            "ok": True,
            "output_dir": os.path.abspath(output_dir),
            "files": output_files,
            "total_outputs": len(output_files),
            "error": None,
        }
    except Exception as e:
        logger.exception("拆分 PDF 失败")
        return {"ok": False, "output_dir": output_dir,
                "files": [], "total_outputs": 0,
                "error": f"拆分 PDF 失败: {e}"}


def get_pdf_info(path: str) -> dict:
    """获取 PDF 文件的元信息。

    使用 pypdf 库读取 PDF 文档的元数据，包括标题、作者、
    创建时间、页数、PDF 版本等。

    Args:
        path: PDF 文件路径

    Returns:
        {
            "ok": bool,
            "info": {
                "filename": str,
                "file_size": int,          # 字节数
                "pages": int,
                "pdf_version": str | None,
                "title": str | None,
                "author": str | None,
                "subject": str | None,
                "creator": str | None,
                "producer": str | None,
                "creation_date": str | None,
                "modification_date": str | None,
                "encrypted": bool,
                "form_fields": [str, ...]  # 表单字段名列表
            } | None,
            "error": str | None
        }
    """
    pypdf, err = _check_library("pypdf", "pypdf")
    if err:
        return {"ok": False, "info": None, "error": err}

    if not os.path.exists(path):
        return {"ok": False, "info": None,
                "error": f"文件不存在: {path}"}

    try:
        reader = pypdf.PdfReader(path)
        meta = reader.metadata

        # 提取表单字段
        form_fields = []
        if reader.get_fields():
            form_fields = list(reader.get_fields().keys())

        # PDF 版本信息
        pdf_version = None
        try:
            pdf_version = reader.pdf_header if hasattr(reader, 'pdf_header') else None
        except Exception:
            pass

        info = {
            "filename": os.path.basename(path),
            "file_size": os.path.getsize(path),
            "pages": len(reader.pages),
            "pdf_version": pdf_version,
            "title": str(meta.title) if meta and meta.title else None,
            "author": str(meta.author) if meta and meta.author else None,
            "subject": str(meta.subject) if meta and meta.subject else None,
            "creator": str(meta.creator) if meta and meta.creator else None,
            "producer": str(meta.producer) if meta and meta.producer else None,
            "creation_date": str(meta.creation_date) if meta and meta.creation_date else None,
            "modification_date": str(meta.modification_date) if meta and meta.modification_date else None,
            "encrypted": reader.is_encrypted if hasattr(reader, 'is_encrypted') else False,
            "form_fields": form_fields,
        }
        return {"ok": True, "info": info, "error": None}
    except Exception as e:
        logger.exception("获取 PDF 元信息失败")
        return {"ok": False, "info": None,
                "error": f"获取 PDF 元信息失败: {e}"}
