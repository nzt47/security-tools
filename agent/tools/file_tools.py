"""工具注册模块 — 文件系统工具（读写、搜索、压缩、比较）"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl):
    """注册所有文件系统工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    from agent.system_tools import (
        read_file, write_file, list_directory,
        get_file_info, search_files,
    )

    @_tools.register("read_file", "读取本地文件的全部内容（文本），支持指定编码。路径可以是绝对路径或相对路径", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
            "max_size_mb": {"type": "integer", "description": "最大读取大小（MB），默认 5"},
            "range": {"type": "string", "description": "可选，行范围，如 \"1-50\" 读取第1到50行"},
        },
        "required": ["path"],
    })
    def _read_file(**kwargs):
        path = kwargs.get("path", "")
        encoding = kwargs.get("encoding", "utf-8")
        max_size_mb = kwargs.get("max_size_mb", 5)
        file_range = kwargs.get("range") or kwargs.get("file_range", "")
        if not path:
            return {"ok": False, "error": "请提供文件路径（path）"}
        return read_file(path, encoding=encoding, max_size_mb=max_size_mb, range=file_range)

    @_tools.register("write_file", "将内容写入本地文件（可创建新文件或覆盖已有文件）。必须同时提供 path（文件路径）和 content（写入内容）两个参数", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（必填），如 /path/to/file.txt"},
            "content": {"type": "string", "description": "写入的内容（必填）"},
            "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
        },
        "required": ["path", "content"],
    })
    def _write_file(**kwargs):
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        encoding = kwargs.get("encoding", "utf-8")
        if not path and not content:
            return {"ok": False, "error": "write_file 需要提供 path（文件路径）和 content（写入内容）两个参数。示例: write_file(path=\"/path/to/file.txt\", content=\"要写入的内容\")"}
        if not path:
            return {"ok": False, "error": f"write_file 缺少 path 参数。请提供文件路径，如: path=\"/path/to/file.txt\". 收到的参数名: {list(kwargs.keys())}"}
        if not content:
            return {"ok": False, "error": "请提供文件内容（content）"}
        # 安全检查：通过 PermissionSystem 校验
        perm = dl._permission.check_action(f"write_file:{path}", f"写入文件 {path}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
        # 通过 PermissionSystem 检查内容
        safety = getattr(dl, '_permission', None)
        if safety:
            try:
                check = safety.check_text(content)
                if check.get("level") == "critical":
                    return {"ok": False, "error": f"内容安全检查未通过: {[m.get('description','') for m in check.get('matches',[])]}", "blocked": True}
            except Exception:
                pass
        return write_file(path, content, encoding=encoding)

    @_tools.register("list_directory", "列出目录中的文件和子目录，支持指定路径和显示隐藏文件", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "show_hidden": {"type": "boolean", "description": "是否显示隐藏文件"},
        },
        "required": ["path"],
    })
    def _list_directory(**kwargs):
        path = kwargs.get("path", ".")
        show_hidden = kwargs.get("show_hidden", False)
        return list_directory(path, show_hidden=show_hidden)

    @_tools.register("get_file_info", "获取文件或目录的详细信息（大小、修改时间、权限等）", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件或目录路径"},
        },
        "required": ["path"],
    })
    def _get_file_info(**kwargs):
        path = kwargs.get("path", "")
        if not path:
            return {"ok": False, "error": "请提供路径（path）"}
        return get_file_info(path)

    @_tools.register("search_files", "按文件名模式搜索文件（支持 glob 通配符，如 *.py, **/*.md）", schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "搜索模式，如 *.txt"},
            "root_path": {"type": "string", "description": "搜索根路径，默认当前目录"},
        },
        "required": ["pattern"],
    })
    def _search_files(**kwargs):
        pattern = kwargs.get("pattern", "")
        root_path = kwargs.get("root_path", ".")
        if not pattern:
            return {"ok": False, "error": "请提供搜索模式（pattern）"}
        # 路径安全校验：防止路径遍历攻击
        try:
            from pathlib import Path
            resolved = Path(root_path).resolve()
            # 检查 pattern 是否包含路径穿越符
            if ".." in pattern.split("/") or ".." in pattern.split("\\"):
                return {"ok": False, "error": "搜索模式包含不安全的路径穿越符（..），已拒绝"}
            # 检查 root_path 是否在有效范围内
            allowed_base = Path(".").resolve()
            if not resolved.exists():
                return {"ok": False, "error": f"搜索路径不存在: {root_path}"}
            if allowed_base not in resolved.parents and resolved != allowed_base:
                return {"ok": False, "error": "搜索路径超出工作目录范围，已拒绝"}
        except Exception as e:
            logger.warning("路径安全校验异常: %s", e)
        return search_files(pattern, root_path=root_path)

    # ════════════════════════════════════════════════════════════
    #  压缩/解压工具
    # ════════════════════════════════════════════════════════════

    from agent.compression_tools import compress, decompress

    @_tools.register("compress", "将文件或目录压缩为 zip 或 tar.gz 格式。支持大文件分块流式处理，可监控进度。output_path 为空时自动在同目录生成 源文件名.zip", schema={
        "type": "object",
        "properties": {
            "source_path": {"type": "string", "description": "要压缩的源文件或目录路径（必填）"},
            "output_path": {"type": "string", "description": "压缩输出路径（可选）。不指定则生成到源文件所在目录：源名.zip 或 源名.tar.gz"},
            "format": {"type": "string", "enum": ["zip", "tar.gz"], "description": "压缩格式：zip（通用兼容性好）或 tar.gz（Linux 常用，压缩率稍高）。默认 zip"},
        },
        "required": ["source_path"],
    })
    def _compress(**kwargs):
        source_path = kwargs.get("source_path", "")
        output_path = kwargs.get("output_path", "")
        fmt = kwargs.get("format", "zip")
        if not source_path:
            return {"ok": False, "error": "请提供源路径（source_path）"}
        return compress(source_path, output_path=output_path, format=fmt)

    @_tools.register("decompress", "解压 zip 或 tar.gz 压缩文件。内置 Zip Slip 攻击防护，安全可靠", schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "压缩文件路径（必填）"},
            "output_dir": {"type": "string", "description": "解压输出目录（可选，默认解压到压缩文件所在目录下的同名文件夹）"},
        },
        "required": ["file_path"],
    })
    def _decompress(**kwargs):
        file_path = kwargs.get("file_path", "")
        output_dir = kwargs.get("output_dir", "")
        if not file_path:
            return {"ok": False, "error": "请提供压缩文件路径（file_path）"}
        return decompress(file_path, output_dir=output_dir)

    # ════════════════════════════════════════════════════════════
    #  文件比较工具
    # ════════════════════════════════════════════════════════════

    from agent.diff_tools import diff_files

    @_tools.register("diff_files", "比较两个文本文件的差异，返回 unified diff 格式（类似 git diff 输出）。自动统计新增行数、删除行数和总变更数。文件大小限制 10MB", schema={
        "type": "object",
        "properties": {
            "path1": {"type": "string", "description": "第一个文件路径（必填，作为对比基准）"},
            "path2": {"type": "string", "description": "第二个文件路径（必填，作为对比目标）"},
            "context_lines": {"type": "integer", "description": "差异上下文显示行数，默认 3。设为 0 只显示变更行，设为较大值显示更多上下文"},
        },
        "required": ["path1", "path2"],
    })
    def _diff_files(**kwargs):
        path1 = kwargs.get("path1", "")
        path2 = kwargs.get("path2", "")
        context_lines = kwargs.get("context_lines", 3)
        if not path1:
            return {"ok": False, "error": "请提供第一个文件路径（path1）"}
        if not path2:
            return {"ok": False, "error": "请提供第二个文件路径（path2）"}
        return diff_files(path1, path2, context_lines=context_lines)
